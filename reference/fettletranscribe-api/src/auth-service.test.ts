import { describe, expect, it } from "vitest";

import { AuthService } from "./auth-service.js";
import { InMemoryDoctorRepository } from "./doctor-repository.js";

describe("AuthService", () => {
  it("lists doctors using search text", async () => {
    const doctorRepository = new InMemoryDoctorRepository();
    await doctorRepository.seedDoctors([
      { seedKey: "dr-rao", name: "Dr. Rao", department: "Cardiology" },
      { seedKey: "dr-mehta", name: "Dr. Mehta", department: "Orthopedics" }
    ]);
    const service = new AuthService(doctorRepository, "test-secret");

    const doctors = await service.listDoctors("cardio");

    expect(doctors).toHaveLength(1);
    expect(doctors[0]?.name).toBe("Dr. Rao");
  });

  it("does not issue a token through doctor id selection compatibility", async () => {
    const doctorRepository = new InMemoryDoctorRepository();
    const [doctor] = await Promise.all([
      doctorRepository.createOrUpdateDoctor({
        name: "Dr. Rao",
        department: "Cardiology"
      })
    ]);
    const service = new AuthService(doctorRepository, "test-secret");

    await service.setDoctorTemporaryPin(doctor.id, "246810");
    await expect(service.loginWithDoctorId(doctor.id)).rejects.toMatchObject({
      code: "PIN_LOGIN_REQUIRED"
    });
  });

  it("changes a temporary PIN and authenticates with the new PIN", async () => {
    const doctorRepository = new InMemoryDoctorRepository();
    const doctor = await doctorRepository.createOrUpdateDoctor({
      name: "Dr. Rao",
      department: "Cardiology"
    });
    const service = new AuthService(doctorRepository, "test-secret");
    await service.setDoctorTemporaryPin(doctor.id, "246810");

    const firstLogin = await service.loginWithPin(doctor.id, "246810");
    expect(firstLogin.status).toBe("pin_change_required");

    if (firstLogin.status !== "pin_change_required") {
      throw new Error("Expected PIN change to be required.");
    }

    const changed = await service.changePin(firstLogin.pinChangeToken, "135790");
    expect(changed.token).toBeTruthy();

    const secondLogin = await service.loginWithPin(doctor.id, "135790");
    expect(secondLogin.status).toBe("authenticated");
  });

  it("does not issue a PIN setup token for PIN-less doctors without bootstrap proof", async () => {
    const doctorRepository = new InMemoryDoctorRepository();
    const doctor = await doctorRepository.createOrUpdateDoctor({
      name: "Dr. Rao",
      department: "Cardiology"
    });
    const service = new AuthService(doctorRepository, "test-secret");

    await expect(service.loginWithPin(doctor.id, "246810")).rejects.toMatchObject({
      code: "PIN_SETUP_REQUIRED"
    });
  });

  it("resolves doctor from public slug path", async () => {
    const doctorRepository = new InMemoryDoctorRepository();
    await doctorRepository.seedDoctors([
      { seedKey: "dr-rao", name: "Dr. Rao", department: "Cardiology" }
    ]);
    const service = new AuthService(doctorRepository, "test-secret");

    const doctor = await service.getDoctorByPublicPath("cardiology", "dr-rao");

    expect(doctor.name).toBe("Dr. Rao");
  });
});
