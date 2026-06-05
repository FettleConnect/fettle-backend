import { MongoClient, type Collection } from "mongodb";

import { type DoctorProfile, doctorProfileSchema } from "@medivoice/shared";

import { slugifyPathSegment } from "./preloaded-doctors.js";

export interface DoctorAuthMetadata {
  pinHash?: string | undefined;
  pinSetAt?: string | undefined;
  mustChangePin: boolean;
  pinResetAt?: string | undefined;
  tokenVersion: number;
  failedPinAttempts: number;
  lockedUntil?: string | undefined;
}

export type DoctorRecord = DoctorProfile & DoctorAuthMetadata;
type DoctorAuthUpdate = {
  [Key in keyof DoctorAuthMetadata]?: DoctorAuthMetadata[Key] | undefined;
};

export interface SeedDoctorInput {
  seedKey: string;
  name: string;
  department: string;
}

export interface DoctorRepository {
  findById(id: string): Promise<DoctorRecord | null>;
  findByPhoneNumber(phoneNumber: string): Promise<DoctorRecord | null>;
  findByEmail(email: string): Promise<DoctorRecord | null>;
  findByDepartmentAndNameSlugs(
    departmentSlug: string,
    doctorNameSlug: string
  ): Promise<DoctorRecord | null>;
  listDoctors(search?: string): Promise<DoctorRecord[]>;
  createOrUpdateDoctor(input: {
    name: string;
    department?: string;
    phoneNumber?: string;
    email?: string;
  }): Promise<DoctorRecord>;
  updateDoctorById(
    doctorId: string,
    input: {
      name: string;
      department?: string;
      phoneNumber?: string;
      email?: string;
    }
  ): Promise<DoctorRecord | null>;
  touchLastLogin(doctorId: string): Promise<DoctorRecord | null>;
  updatePinAuth(doctorId: string, input: DoctorAuthUpdate): Promise<DoctorRecord | null>;
  recordFailedPinAttempt(input: {
    doctorId: string;
    lockThreshold: number;
    lockedUntil: string;
  }): Promise<DoctorRecord | null>;
  seedDoctors(doctors: SeedDoctorInput[]): Promise<number>;
  ensureIndexes(): Promise<void>;
}

interface DoctorDocument {
  _id: string;
  seedKey?: string;
  name: string;
  nameSlug?: string;
  department?: string;
  departmentSlug?: string;
  phoneNumber?: string;
  email?: string;
  createdAt: string;
  lastLoginAt: string;
  pinHash?: string;
  pinSetAt?: string;
  mustChangePin?: boolean;
  pinResetAt?: string;
  tokenVersion?: number;
  failedPinAttempts?: number;
  lockedUntil?: string;
}

function doctorSlugKey(input: { name: string; department: string }): string {
  return `${slugifyPathSegment(input.department)}::${slugifyPathSegment(input.name)}`;
}

export class InMemoryDoctorRepository implements DoctorRepository {
  private readonly doctors = new Map<string, DoctorRecord>();
  private readonly doctorsBySeedKey = new Map<string, string>();
  private readonly doctorsByPhoneNumber = new Map<string, string>();
  private readonly doctorsByEmail = new Map<string, string>();
  private readonly doctorsBySlug = new Map<string, string>();

  async findById(id: string): Promise<DoctorRecord | null> {
    return this.doctors.get(id) ?? null;
  }

  async findByPhoneNumber(phoneNumber: string): Promise<DoctorRecord | null> {
    const doctorId = this.doctorsByPhoneNumber.get(phoneNumber);
    return doctorId ? (this.doctors.get(doctorId) ?? null) : null;
  }

  async findByEmail(email: string): Promise<DoctorRecord | null> {
    const doctorId = this.doctorsByEmail.get(email);
    return doctorId ? (this.doctors.get(doctorId) ?? null) : null;
  }

  async findByDepartmentAndNameSlugs(
    departmentSlug: string,
    doctorNameSlug: string
  ): Promise<DoctorRecord | null> {
    const doctorId = this.doctorsBySlug.get(`${departmentSlug}::${doctorNameSlug}`);
    return doctorId ? (this.doctors.get(doctorId) ?? null) : null;
  }

  async listDoctors(search?: string): Promise<DoctorRecord[]> {
    const normalizedSearch = search?.trim().toLowerCase() ?? "";
    return [...this.doctors.values()]
      .filter((doctor) => {
        if (!normalizedSearch) {
          return true;
        }

        const doctorName = doctor.name.toLowerCase();
        const department = doctor.department?.toLowerCase() ?? "";
        return (
          doctorName.includes(normalizedSearch) ||
          department.includes(normalizedSearch)
        );
      })
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  async createOrUpdateDoctor(input: {
    name: string;
    department?: string;
    phoneNumber?: string;
    email?: string;
  }): Promise<DoctorRecord> {
    const existing =
      (input.phoneNumber
        ? await this.findByPhoneNumber(input.phoneNumber)
        : null) ??
      (input.email ? await this.findByEmail(input.email) : null);
    const now = new Date().toISOString();

    if (existing) {
      const updatedDoctor: DoctorRecord = {
        ...existing,
        name: input.name,
        ...(input.department
          ? { department: input.department }
          : existing.department
            ? { department: existing.department }
            : {}),
        ...(input.phoneNumber
          ? { phoneNumber: input.phoneNumber }
          : existing.phoneNumber
            ? { phoneNumber: existing.phoneNumber }
            : {}),
        ...(input.email
          ? { email: input.email }
          : existing.email
            ? { email: existing.email }
            : {}),
        lastLoginAt: now
      };

      this.doctors.set(existing.id, updatedDoctor);
      if (
        existing.phoneNumber &&
        existing.phoneNumber !== updatedDoctor.phoneNumber
      ) {
        this.doctorsByPhoneNumber.delete(existing.phoneNumber);
      }
      if (existing.email && existing.email !== updatedDoctor.email) {
        this.doctorsByEmail.delete(existing.email);
      }
      if (updatedDoctor.phoneNumber) {
        this.doctorsByPhoneNumber.set(updatedDoctor.phoneNumber, existing.id);
      }
      if (updatedDoctor.email) {
        this.doctorsByEmail.set(updatedDoctor.email, existing.id);
      }
      if (updatedDoctor.department) {
        this.doctorsBySlug.set(
          doctorSlugKey({
            name: updatedDoctor.name,
            department: updatedDoctor.department
          }),
          updatedDoctor.id
        );
      }
      return updatedDoctor;
    }

    const doctor: DoctorRecord = {
      id: crypto.randomUUID(),
      name: input.name,
      ...(input.department ? { department: input.department } : {}),
      ...(input.phoneNumber ? { phoneNumber: input.phoneNumber } : {}),
      ...(input.email ? { email: input.email } : {}),
      createdAt: now,
      lastLoginAt: now,
      mustChangePin: true,
      tokenVersion: 0,
      failedPinAttempts: 0
    };

    this.doctors.set(doctor.id, doctor);
    if (doctor.phoneNumber) {
      this.doctorsByPhoneNumber.set(doctor.phoneNumber, doctor.id);
    }
    if (doctor.email) {
      this.doctorsByEmail.set(doctor.email, doctor.id);
    }
    if (doctor.department) {
      this.doctorsBySlug.set(
        doctorSlugKey({
          name: doctor.name,
          department: doctor.department
        }),
        doctor.id
      );
    }
    return doctor;
  }

  async updateDoctorById(
    doctorId: string,
    input: {
      name: string;
      department?: string;
      phoneNumber?: string;
      email?: string;
    }
  ): Promise<DoctorRecord | null> {
    const existing = this.doctors.get(doctorId);

    if (!existing) {
      return null;
    }

    const updatedDoctor: DoctorRecord = {
      ...existing,
      name: input.name,
      ...(input.department ? { department: input.department } : {}),
      ...(input.phoneNumber ? { phoneNumber: input.phoneNumber } : {}),
      ...(input.email ? { email: input.email } : {})
    };

    if (existing.phoneNumber && existing.phoneNumber !== updatedDoctor.phoneNumber) {
      this.doctorsByPhoneNumber.delete(existing.phoneNumber);
    }
    if (existing.email && existing.email !== updatedDoctor.email) {
      this.doctorsByEmail.delete(existing.email);
    }
    if (existing.department) {
      this.doctorsBySlug.delete(
        doctorSlugKey({
          name: existing.name,
          department: existing.department
        })
      );
    }

    this.doctors.set(doctorId, updatedDoctor);

    if (updatedDoctor.phoneNumber) {
      this.doctorsByPhoneNumber.set(updatedDoctor.phoneNumber, doctorId);
    }
    if (updatedDoctor.email) {
      this.doctorsByEmail.set(updatedDoctor.email, doctorId);
    }
    if (updatedDoctor.department) {
      this.doctorsBySlug.set(
        doctorSlugKey({
          name: updatedDoctor.name,
          department: updatedDoctor.department
        }),
        updatedDoctor.id
      );
    }

    return updatedDoctor;
  }

  async touchLastLogin(doctorId: string): Promise<DoctorRecord | null> {
    const doctor = this.doctors.get(doctorId);

    if (!doctor) {
      return null;
    }

    const updatedDoctor: DoctorRecord = {
      ...doctor,
      lastLoginAt: new Date().toISOString()
    };
    this.doctors.set(doctorId, updatedDoctor);
    return updatedDoctor;
  }

  async updatePinAuth(
    doctorId: string,
    input: DoctorAuthUpdate
  ): Promise<DoctorRecord | null> {
    const doctor = this.doctors.get(doctorId);

    if (!doctor) {
      return null;
    }

    const updatedDoctor: DoctorRecord = {
      ...doctor,
      ...input,
      mustChangePin: input.mustChangePin ?? doctor.mustChangePin,
      tokenVersion: input.tokenVersion ?? doctor.tokenVersion,
      failedPinAttempts: input.failedPinAttempts ?? doctor.failedPinAttempts
    };

    for (const key of ["pinHash", "pinSetAt", "pinResetAt", "lockedUntil"] as const) {
      if (Object.prototype.hasOwnProperty.call(input, key) && input[key] === undefined) {
        delete updatedDoctor[key];
      }
    }

    this.doctors.set(doctorId, updatedDoctor);
    return updatedDoctor;
  }

  async recordFailedPinAttempt(input: {
    doctorId: string;
    lockThreshold: number;
    lockedUntil: string;
  }): Promise<DoctorRecord | null> {
    const doctor = this.doctors.get(input.doctorId);

    if (!doctor) {
      return null;
    }

    const failedPinAttempts = doctor.failedPinAttempts + 1;
    const updatedDoctor: DoctorRecord = {
      ...doctor,
      failedPinAttempts,
      ...(failedPinAttempts >= input.lockThreshold
        ? { lockedUntil: input.lockedUntil }
        : {})
    };

    this.doctors.set(input.doctorId, updatedDoctor);
    return updatedDoctor;
  }

  async seedDoctors(doctors: SeedDoctorInput[]): Promise<number> {
    let insertedCount = 0;
    const now = new Date().toISOString();

    for (const doctor of doctors) {
      const key = doctorSlugKey(doctor);
      const existingDoctorId =
        this.doctorsBySeedKey.get(doctor.seedKey) ?? this.doctorsBySlug.get(key);

      if (existingDoctorId) {
        const existingDoctor = this.doctors.get(existingDoctorId);

        if (!existingDoctor) {
          continue;
        }

        this.doctors.set(existingDoctor.id, {
          ...existingDoctor,
          name: doctor.name,
          department: doctor.department
        });
        this.doctorsBySeedKey.set(doctor.seedKey, existingDoctor.id);
        this.doctorsBySlug.set(key, existingDoctor.id);
        continue;
      }

      const profile: DoctorRecord = {
        id: crypto.randomUUID(),
        name: doctor.name,
        department: doctor.department,
        createdAt: now,
        lastLoginAt: now,
        mustChangePin: true,
        tokenVersion: 0,
        failedPinAttempts: 0
      };

      this.doctors.set(profile.id, profile);
      this.doctorsBySeedKey.set(doctor.seedKey, profile.id);
      this.doctorsBySlug.set(key, profile.id);
      insertedCount += 1;
    }

    return insertedCount;
  }

  async ensureIndexes(): Promise<void> {
    return;
  }
}

export class MongoDoctorRepository implements DoctorRepository {
  private readonly collection: Collection<DoctorDocument>;

  constructor(client: MongoClient, dbName = "medivoice_rewrite") {
    const database = client.db(dbName);
    this.collection = database.collection<DoctorDocument>("doctors");
  }

  async findById(id: string): Promise<DoctorRecord | null> {
    const document = await this.collection.findOne({ _id: id });
    return document ? parseDoctorDocument(document) : null;
  }

  async findByPhoneNumber(phoneNumber: string): Promise<DoctorRecord | null> {
    const document = await this.collection.findOne({ phoneNumber });
    return document ? parseDoctorDocument(document) : null;
  }

  async findByEmail(email: string): Promise<DoctorRecord | null> {
    const document = await this.collection.findOne({ email });
    return document ? parseDoctorDocument(document) : null;
  }

  async findByDepartmentAndNameSlugs(
    departmentSlug: string,
    doctorNameSlug: string
  ): Promise<DoctorRecord | null> {
    const document = await this.collection.findOne({
      departmentSlug,
      nameSlug: doctorNameSlug
    });
    return document ? parseDoctorDocument(document) : null;
  }

  async listDoctors(search?: string): Promise<DoctorRecord[]> {
    const normalizedSearch = search?.trim();
    const query = normalizedSearch
      ? {
          $or: [
            { name: { $regex: escapeRegExp(normalizedSearch), $options: "i" } },
            {
              department: {
                $regex: escapeRegExp(normalizedSearch),
                $options: "i"
              }
            }
          ]
        }
      : {};

    const documents = await this.collection
      .find(query)
      .sort({ name: 1 })
      .limit(100)
      .toArray();
    return documents.map(parseDoctorDocument);
  }

  async createOrUpdateDoctor(input: {
    name: string;
    department?: string;
    phoneNumber?: string;
    email?: string;
  }): Promise<DoctorRecord> {
    const existing =
      (input.phoneNumber
        ? await this.findByPhoneNumber(input.phoneNumber)
        : null) ??
      (input.email ? await this.findByEmail(input.email) : null);
    const now = new Date().toISOString();

    if (existing) {
      const nextDepartment = input.department ?? existing.department;
      const nextPhoneNumber = input.phoneNumber ?? existing.phoneNumber;
      const nextEmail = input.email ?? existing.email;
      const nextDocument: DoctorDocument = {
        _id: existing.id,
        name: input.name,
        ...(nextDepartment
          ? {
              department: nextDepartment,
              departmentSlug: slugifyPathSegment(nextDepartment)
            }
          : {}),
        nameSlug: slugifyPathSegment(input.name),
        ...(nextPhoneNumber ? { phoneNumber: nextPhoneNumber } : {}),
        ...(nextEmail ? { email: nextEmail } : {}),
        createdAt: existing.createdAt,
        lastLoginAt: now,
        mustChangePin: existing.mustChangePin,
        tokenVersion: existing.tokenVersion,
        failedPinAttempts: existing.failedPinAttempts,
        ...(existing.pinHash ? { pinHash: existing.pinHash } : {}),
        ...(existing.pinSetAt ? { pinSetAt: existing.pinSetAt } : {}),
        ...(existing.pinResetAt ? { pinResetAt: existing.pinResetAt } : {}),
        ...(existing.lockedUntil ? { lockedUntil: existing.lockedUntil } : {})
      };

      await this.collection.updateOne(
        { _id: existing.id },
        {
          $set: {
            name: nextDocument.name,
            ...(nextDocument.nameSlug ? { nameSlug: nextDocument.nameSlug } : {}),
            ...(nextDocument.department
              ? { department: nextDocument.department }
              : {}),
            ...(nextDocument.departmentSlug
              ? { departmentSlug: nextDocument.departmentSlug }
              : {}),
            ...(nextDocument.phoneNumber
              ? { phoneNumber: nextDocument.phoneNumber }
              : {}),
            ...(nextDocument.email ? { email: nextDocument.email } : {}),
            lastLoginAt: nextDocument.lastLoginAt
          }
        }
      );

      return parseDoctorDocument(nextDocument);
    }

    const doctorDocument: DoctorDocument = {
      _id: crypto.randomUUID(),
      name: input.name,
      nameSlug: slugifyPathSegment(input.name),
      ...(input.department
        ? {
            department: input.department,
            departmentSlug: slugifyPathSegment(input.department)
          }
        : {}),
      ...(input.phoneNumber ? { phoneNumber: input.phoneNumber } : {}),
      ...(input.email ? { email: input.email } : {}),
      createdAt: now,
      lastLoginAt: now,
      mustChangePin: true,
      tokenVersion: 0,
      failedPinAttempts: 0
    };

    await this.collection.insertOne(doctorDocument);
    return parseDoctorDocument(doctorDocument);
  }

  async updateDoctorById(
    doctorId: string,
    input: {
      name: string;
      department?: string;
      phoneNumber?: string;
      email?: string;
    }
  ): Promise<DoctorRecord | null> {
    const existing = await this.findById(doctorId);

    if (!existing) {
      return null;
    }

    const result = await this.collection.findOneAndUpdate(
      { _id: doctorId },
      {
        $set: {
          name: input.name,
          nameSlug: slugifyPathSegment(input.name),
          ...(input.department
            ? {
                department: input.department,
                departmentSlug: slugifyPathSegment(input.department)
              }
            : {}),
          ...(input.phoneNumber ? { phoneNumber: input.phoneNumber } : {}),
          ...(input.email ? { email: input.email } : {})
        },
        $unset: {
          ...(input.department ? {} : { department: "", departmentSlug: "" }),
          ...(input.phoneNumber ? {} : { phoneNumber: "" }),
          ...(input.email ? {} : { email: "" })
        }
      },
      {
        returnDocument: "after"
      }
    );

    return result ? parseDoctorDocument(result) : null;
  }

  async touchLastLogin(doctorId: string): Promise<DoctorRecord | null> {
    const now = new Date().toISOString();
    const result = await this.collection.findOneAndUpdate(
      { _id: doctorId },
      {
        $set: { lastLoginAt: now }
      },
      { returnDocument: "after" }
    );

    return result ? parseDoctorDocument(result) : null;
  }

  async updatePinAuth(
    doctorId: string,
    input: DoctorAuthUpdate
  ): Promise<DoctorRecord | null> {
    const set: Partial<DoctorDocument> = {};
    const unset: Record<string, ""> = {};

    for (const [key, value] of Object.entries(input) as Array<
      [keyof DoctorAuthMetadata, DoctorAuthMetadata[keyof DoctorAuthMetadata] | undefined]
    >) {
      if (value === undefined) {
        unset[key] = "";
      } else {
        set[key] = value as never;
      }
    }

    const result = await this.collection.findOneAndUpdate(
      { _id: doctorId },
      {
        ...(Object.keys(set).length > 0 ? { $set: set } : {}),
        ...(Object.keys(unset).length > 0 ? { $unset: unset } : {})
      },
      { returnDocument: "after" }
    );

    return result ? parseDoctorDocument(result) : null;
  }

  async recordFailedPinAttempt(input: {
    doctorId: string;
    lockThreshold: number;
    lockedUntil: string;
  }): Promise<DoctorRecord | null> {
    const nextAttempts = {
      $add: [{ $ifNull: ["$failedPinAttempts", 0] }, 1]
    };
    const result = await this.collection.findOneAndUpdate(
      { _id: input.doctorId },
      [
        {
          $set: {
            failedPinAttempts: nextAttempts,
            lockedUntil: {
              $cond: [
                { $gte: [nextAttempts, input.lockThreshold] },
                input.lockedUntil,
                "$lockedUntil"
              ]
            }
          }
        }
      ],
      { returnDocument: "after" }
    );

    return result ? parseDoctorDocument(result) : null;
  }

  async seedDoctors(doctors: SeedDoctorInput[]): Promise<number> {
    let insertedCount = 0;
    const now = new Date().toISOString();

    for (const doctor of doctors) {
      const departmentSlug = slugifyPathSegment(doctor.department);
      const nameSlug = slugifyPathSegment(doctor.name);
      const result = await this.collection.updateOne(
        {
          $or: [
            { seedKey: doctor.seedKey },
            { departmentSlug, nameSlug }
          ]
        },
        {
          $set: {
            seedKey: doctor.seedKey,
            name: doctor.name,
            department: doctor.department,
            departmentSlug,
            nameSlug
          },
          $setOnInsert: {
            _id: crypto.randomUUID(),
            createdAt: now,
            lastLoginAt: now,
            mustChangePin: true,
            tokenVersion: 0,
            failedPinAttempts: 0
          }
        },
        { upsert: true }
      );

      insertedCount += result.upsertedCount;
    }

    return insertedCount;
  }

  async ensureIndexes(): Promise<void> {
    await this.collection.createIndex(
      { seedKey: 1 },
      {
        unique: true,
        partialFilterExpression: { seedKey: { $exists: true } }
      }
    );
    await this.collection.createIndex(
      { phoneNumber: 1 },
      {
        unique: true,
        partialFilterExpression: { phoneNumber: { $exists: true } }
      }
    );
    await this.collection.createIndex(
      { email: 1 },
      {
        unique: true,
        partialFilterExpression: { email: { $exists: true } }
      }
    );
    await this.collection.createIndex(
      { departmentSlug: 1, nameSlug: 1 },
      {
        unique: true,
        partialFilterExpression: {
          departmentSlug: { $exists: true },
          nameSlug: { $exists: true }
        }
      }
    );
    await this.collection.createIndex({ name: 1 });
    await this.collection.createIndex({ department: 1 });
  }
}

function parseDoctorDocument(document: DoctorDocument): DoctorRecord {
  const now = new Date().toISOString();
  const mustChangePin = document.mustChangePin ?? !document.pinHash;
  const lockedUntil = document.lockedUntil &&
    new Date(document.lockedUntil).getTime() > Date.now()
    ? document.lockedUntil
    : undefined;
  const safeProfile = doctorProfileSchema.parse({
    id: document._id,
    name: document.name,
    ...(document.department ? { department: document.department } : {}),
    ...(document.phoneNumber ? { phoneNumber: document.phoneNumber } : {}),
    ...(document.email ? { email: document.email } : {}),
    createdAt: document.createdAt,
    lastLoginAt: document.lastLoginAt ?? now,
    pinStatus: lockedUntil
      ? "locked"
      : !document.pinHash
        ? "not_set"
        : mustChangePin
          ? "change_required"
          : "active",
    ...(document.pinSetAt ? { pinSetAt: document.pinSetAt } : {}),
    ...(lockedUntil ? { lockedUntil } : {})
  });

  return {
    ...safeProfile,
    ...(document.pinHash ? { pinHash: document.pinHash } : {}),
    ...(document.pinSetAt ? { pinSetAt: document.pinSetAt } : {}),
    mustChangePin,
    ...(document.pinResetAt ? { pinResetAt: document.pinResetAt } : {}),
    tokenVersion: document.tokenVersion ?? 0,
    failedPinAttempts: document.failedPinAttempts ?? 0,
    ...(document.lockedUntil ? { lockedUntil: document.lockedUntil } : {})
  };
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
