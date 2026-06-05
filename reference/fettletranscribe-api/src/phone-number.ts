import { BadRequestError } from "./errors.js";

export function normalizeIndianPhoneNumber(value: string): string {
  const digits = value.replace(/\D/g, "");
  const local =
    digits.startsWith("91") && digits.length > 10 ? digits.slice(2) : digits;

  if (local.length !== 10) {
    throw new BadRequestError("Mobile number must be exactly 10 digits.");
  }

  return local;
}

export function toIndianE164PhoneNumber(value: string): string {
  return `+91${normalizeIndianPhoneNumber(value)}`;
}
