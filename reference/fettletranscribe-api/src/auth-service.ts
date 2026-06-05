import { scrypt as scryptCallback, randomBytes, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";

import { type DoctorProfile } from "@medivoice/shared";

import { type DoctorRecord, type DoctorRepository } from "./doctor-repository.js";
import {
  BadRequestError,
  ConflictError,
  LockedError,
  NotFoundError,
  UnauthorizedError
} from "./errors.js";
import {
  issueDoctorToken,
  issuePinChangeToken,
  verifyDoctorToken,
  verifyPinChangeToken
} from "./auth-token.js";

const scrypt = promisify(scryptCallback);
const maxFailedPinAttempts = 5;
const pinLockDurationMs = 5 * 60 * 1000;

export class AuthService {
  constructor(
    private readonly doctorRepository: DoctorRepository,
    private readonly jwtSecret: string
  ) {}

  async getDoctorFromToken(token: string): Promise<DoctorProfile> {
    const payload = verifyDoctorToken(token, this.jwtSecret);

    if (!payload) {
      throw new NotFoundError("Your session has expired. Please log in again.");
    }

    const doctor = await this.doctorRepository.findById(payload.doctorId);

    if (!doctor || doctor.tokenVersion !== payload.tokenVersion) {
      throw new NotFoundError("Your session has expired. Please log in again.");
    }

    return sanitizeDoctor(doctor);
  }

  async listDoctors(search?: string): Promise<DoctorProfile[]> {
    const doctors = await this.doctorRepository.listDoctors(search);
    return doctors.map(sanitizeDoctor);
  }

  async loginWithDoctorId(doctorId: string): Promise<{
    doctor: DoctorProfile;
    token: string;
  }> {
    const doctor = await this.doctorRepository.findById(doctorId);

    if (!doctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    throw new ConflictError("PIN login is required before sign in.", "PIN_LOGIN_REQUIRED");
  }

  async loginWithPin(doctorId: string, pin: string): Promise<
    | { status: "authenticated"; doctor: DoctorProfile; token: string }
    | { status: "pin_change_required"; doctor: DoctorProfile; pinChangeToken: string }
  > {
    validatePinFormat(pin);
    const doctor = await this.doctorRepository.findById(doctorId);

    if (!doctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    if (!doctor.pinHash) {
      throw new ConflictError("PIN setup is required before sign in.", "PIN_SETUP_REQUIRED");
    }

    if (isLocked(doctor)) {
      throw new LockedError("Too many attempts. Try again in a few minutes.", "PIN_LOCKED");
    }

    const pinMatches = await verifyPin(pin, doctor.pinHash);

    if (!pinMatches) {
      await this.doctorRepository.recordFailedPinAttempt({
        doctorId: doctor.id,
        lockThreshold: maxFailedPinAttempts,
        lockedUntil: new Date(Date.now() + pinLockDurationMs).toISOString()
      });
      throw new UnauthorizedError("PIN did not match this doctor account.", "INVALID_PIN");
    }

    const clearedDoctor = await this.doctorRepository.updatePinAuth(doctor.id, {
      failedPinAttempts: 0,
      lockedUntil: undefined
    });

    if (!clearedDoctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    if (clearedDoctor.mustChangePin) {
      return {
        status: "pin_change_required",
        doctor: sanitizeDoctor(clearedDoctor),
        pinChangeToken: issuePinChangeToken(clearedDoctor, this.jwtSecret)
      };
    }

    const loggedInDoctor = await this.doctorRepository.touchLastLogin(doctor.id);

    if (!loggedInDoctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    return {
      status: "authenticated",
      doctor: sanitizeDoctor(loggedInDoctor),
      token: issueDoctorToken(loggedInDoctor, this.jwtSecret)
    };
  }

  async changePin(pinChangeToken: string, newPin: string): Promise<{
    doctor: DoctorProfile;
    token: string;
  }> {
    validatePinStrength(newPin);
    const payload = verifyPinChangeToken(pinChangeToken, this.jwtSecret);

    if (!payload) {
      throw new UnauthorizedError("PIN setup link has expired.", "INVALID_PIN_CHANGE_TOKEN");
    }

    const doctor = await this.doctorRepository.findById(payload.doctorId);

    if (!doctor || doctor.tokenVersion !== payload.tokenVersion || !doctor.mustChangePin) {
      throw new UnauthorizedError("PIN setup link has expired.", "INVALID_PIN_CHANGE_TOKEN");
    }

    if (doctor.pinHash && await verifyPin(newPin, doctor.pinHash)) {
      throw new BadRequestError("New PIN must be different from the temporary PIN.", "INVALID_PIN_FORMAT");
    }

    const now = new Date().toISOString();
    const updatedDoctor = await this.doctorRepository.updatePinAuth(doctor.id, {
      pinHash: await hashPin(newPin),
      pinSetAt: now,
      mustChangePin: false,
      failedPinAttempts: 0,
      lockedUntil: undefined
    });

    if (!updatedDoctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    const loggedInDoctor = await this.doctorRepository.touchLastLogin(updatedDoctor.id);
    if (!loggedInDoctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    return {
      doctor: sanitizeDoctor(loggedInDoctor),
      token: issueDoctorToken(loggedInDoctor, this.jwtSecret)
    };
  }

  async resetDoctorPin(doctorId: string): Promise<{
    doctor: DoctorProfile;
    temporaryPin: string;
  }> {
    const doctor = await this.doctorRepository.findById(doctorId);

    if (!doctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    const temporaryPin = generateTemporaryPin();
    const now = new Date().toISOString();
    const updatedDoctor = await this.doctorRepository.updatePinAuth(doctor.id, {
      pinHash: await hashPin(temporaryPin),
      pinSetAt: now,
      pinResetAt: now,
      mustChangePin: true,
      tokenVersion: doctor.tokenVersion + 1,
      failedPinAttempts: 0,
      lockedUntil: undefined
    });

    if (!updatedDoctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    return {
      doctor: sanitizeDoctor(updatedDoctor),
      temporaryPin
    };
  }

  async clearDoctorPinLock(doctorId: string): Promise<DoctorProfile> {
    const updatedDoctor = await this.doctorRepository.updatePinAuth(doctorId, {
      failedPinAttempts: 0,
      lockedUntil: undefined
    });

    if (!updatedDoctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    return sanitizeDoctor(updatedDoctor);
  }

  async setDoctorTemporaryPin(doctorId: string, temporaryPin: string): Promise<DoctorProfile> {
    validatePinStrength(temporaryPin);
    const doctor = await this.doctorRepository.findById(doctorId);

    if (!doctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    const now = new Date().toISOString();
    const updatedDoctor = await this.doctorRepository.updatePinAuth(doctor.id, {
      pinHash: await hashPin(temporaryPin),
      pinSetAt: now,
      pinResetAt: now,
      mustChangePin: true,
      tokenVersion: doctor.tokenVersion + 1,
      failedPinAttempts: 0,
      lockedUntil: undefined
    });

    if (!updatedDoctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    return sanitizeDoctor(updatedDoctor);
  }

  issueTokenForTest(doctor: DoctorRecord): string {
    const token = issueDoctorToken(doctor, this.jwtSecret);
    return token;
  }

  async getDoctorByPublicPath(
    departmentSlug: string,
    doctorNameSlug: string
  ): Promise<DoctorProfile> {
    const doctor = await this.doctorRepository.findByDepartmentAndNameSlugs(
      departmentSlug,
      doctorNameSlug
    );

    if (!doctor) {
      throw new NotFoundError("Doctor intake link not found.");
    }

    return sanitizeDoctor(doctor);
  }

  async getDoctorById(doctorId: string): Promise<DoctorProfile> {
    const doctor = await this.doctorRepository.findById(doctorId);

    if (!doctor) {
      throw new NotFoundError("Doctor account not found.");
    }

    return sanitizeDoctor(doctor);
  }

  async createDoctor(input: {
    name: string;
    department?: string;
    phoneNumber?: string;
    email?: string;
  }): Promise<DoctorProfile> {
    const doctor = await this.doctorRepository.createOrUpdateDoctor(input);
    return sanitizeDoctor(doctor);
  }

  async updateDoctorById(
    doctorId: string,
    input: {
      name: string;
      department?: string;
      phoneNumber?: string;
      email?: string;
    }
  ): Promise<DoctorProfile> {
    const updated = await this.doctorRepository.updateDoctorById(doctorId, input);

    if (!updated) {
      throw new NotFoundError("Doctor account not found.");
    }

    return sanitizeDoctor(updated);
  }
}

function sanitizeDoctor(doctor: DoctorRecord): DoctorProfile {
  return {
    id: doctor.id,
    name: doctor.name,
    ...(doctor.department ? { department: doctor.department } : {}),
    ...(doctor.phoneNumber ? { phoneNumber: doctor.phoneNumber } : {}),
    ...(doctor.email ? { email: doctor.email } : {}),
    createdAt: doctor.createdAt,
    lastLoginAt: doctor.lastLoginAt,
    pinStatus: doctor.lockedUntil && new Date(doctor.lockedUntil).getTime() > Date.now()
      ? "locked"
      : !doctor.pinHash
        ? "not_set"
        : doctor.mustChangePin
          ? "change_required"
          : "active",
    ...(doctor.pinSetAt ? { pinSetAt: doctor.pinSetAt } : {}),
    ...(doctor.lockedUntil ? { lockedUntil: doctor.lockedUntil } : {})
  };
}

function validatePinFormat(pin: string): void {
  if (!/^\d{6}$/.test(pin)) {
    throw new BadRequestError("PIN must be exactly 6 digits.", "INVALID_PIN_FORMAT");
  }
}

function validatePinStrength(pin: string): void {
  validatePinFormat(pin);

  if (/^(\d)\1{5}$/.test(pin) || pin === "123456" || pin === "654321") {
    throw new BadRequestError("Choose a less obvious 6-digit PIN.", "INVALID_PIN_FORMAT");
  }
}

function isLocked(doctor: DoctorRecord): boolean {
  return Boolean(
    doctor.lockedUntil && new Date(doctor.lockedUntil).getTime() > Date.now()
  );
}

async function hashPin(pin: string): Promise<string> {
  const salt = randomBytes(16).toString("base64url");
  const derivedKey = (await scrypt(pin, salt, 64)) as Buffer;
  return `scrypt:${salt}:${derivedKey.toString("base64url")}`;
}

async function verifyPin(pin: string, storedHash: string): Promise<boolean> {
  const [algorithm, salt, hash] = storedHash.split(":");

  if (algorithm !== "scrypt" || !salt || !hash) {
    return false;
  }

  const expected = Buffer.from(hash, "base64url");
  const actual = (await scrypt(pin, salt, expected.length)) as Buffer;

  if (actual.length !== expected.length) {
    return false;
  }

  return timingSafeEqual(actual, expected);
}

function generateTemporaryPin(): string {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const candidate = randomBytes(4).readUInt32BE(0).toString().slice(0, 6).padStart(6, "0");
    try {
      validatePinStrength(candidate);
      return candidate;
    } catch {
      continue;
    }
  }

  return "246810";
}
