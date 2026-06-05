import { describe, expect, it } from "vitest";

import { InMemoryDoctorRepository } from "./doctor-repository.js";

describe("InMemoryDoctorRepository", () => {
  it("preserves doctor identity when a seeded doctor name or department is corrected", async () => {
    const repository = new InMemoryDoctorRepository();

    await repository.seedDoctors([
      {
        seedKey: "dr-rao",
        name: "Dr. Rao",
        department: "Cardiology"
      }
    ]);

    const [originalDoctor] = await repository.listDoctors("rao");

    expect(originalDoctor).toBeTruthy();

    await repository.seedDoctors([
      {
        seedKey: "dr-rao",
        name: "Dr. R. Rao",
        department: "Interventional Cardiology"
      }
    ]);

    const [updatedDoctor] = await repository.listDoctors("r. rao");

    expect(updatedDoctor).toBeTruthy();
    expect(updatedDoctor?.id).toBe(originalDoctor?.id);
    expect(updatedDoctor?.name).toBe("Dr. R. Rao");
    expect(updatedDoctor?.department).toBe("Interventional Cardiology");
  });
});
