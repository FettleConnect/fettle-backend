import { describe, expect, it } from "vitest";

import { issueDoctorToken, verifyDoctorToken } from "./auth-token.js";

describe("auth-token", () => {
  it("defaults doctor tokens to a 30 day lifetime", () => {
    const token = issueDoctorToken(
      {
        id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        name: "Dr. Rao",
        createdAt: "2026-03-18T00:00:00.000Z",
        lastLoginAt: "2026-03-18T00:00:00.000Z"
      },
      "test-secret"
    );

    const encodedPayload = token.split(".")[1];

    expect(encodedPayload).toBeTruthy();

    const payload = JSON.parse(
      Buffer.from(encodedPayload!, "base64url").toString("utf8")
    ) as { exp: number };
    const lifetimeSeconds = payload.exp - Math.floor(Date.now() / 1000);

    expect(lifetimeSeconds).toBeGreaterThanOrEqual(30 * 24 * 60 * 60 - 5);
    expect(lifetimeSeconds).toBeLessThanOrEqual(30 * 24 * 60 * 60);
  });

  it("rejects signatures with mismatched lengths without throwing", () => {
    const token = issueDoctorToken(
      {
        id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        name: "Dr. Rao",
        email: "dr.rao@example.com",
        createdAt: "2026-03-18T00:00:00.000Z",
        lastLoginAt: "2026-03-18T00:00:00.000Z"
      },
      "test-secret"
    );

    const [header, payload] = token.split(".");
    const malformed = `${header}.${payload}.short`;

    expect(verifyDoctorToken(malformed, "test-secret")).toBeNull();
  });
});
