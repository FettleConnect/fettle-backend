import { Router } from "express";
import { z } from "zod";

import { listDoctorsQuerySchema } from "@medivoice/shared";

import { type AuthService } from "../auth-service.js";

const upsertDoctorRequestSchema = z.object({
  name: z.string().trim().min(1),
  department: z.string().trim().min(1).optional(),
  phoneNumber: z.string().trim().min(10).max(20).optional(),
  email: z.string().trim().email().optional()
});

export function createInternalAdminDoctorRouter({
  authService
}: {
  authService: AuthService;
}): Router {
  const router = Router();

  router.get("/internal/admin/doctors", async (request, response, next) => {
    try {
      const { search } = listDoctorsQuerySchema.parse(request.query);
      const doctors = await authService.listDoctors(search);
      response.json({ doctors });
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/doctors/:doctorId", async (request, response, next) => {
    try {
      const doctor = await authService.getDoctorById(request.params.doctorId);
      response.json({ doctor });
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/doctors", async (request, response, next) => {
    try {
      const input = upsertDoctorRequestSchema.parse(request.body);
      const doctor = await authService.createDoctor({
        name: input.name,
        ...(input.department ? { department: input.department } : {}),
        ...(input.phoneNumber ? { phoneNumber: input.phoneNumber } : {}),
        ...(input.email ? { email: input.email } : {})
      });
      response.status(201).json({ doctor });
    } catch (error) {
      next(error);
    }
  });

  router.put("/internal/admin/doctors/:doctorId", async (request, response, next) => {
    try {
      const input = upsertDoctorRequestSchema.parse(request.body);
      const doctor = await authService.updateDoctorById(
        request.params.doctorId,
        {
          name: input.name,
          ...(input.department ? { department: input.department } : {}),
          ...(input.phoneNumber ? { phoneNumber: input.phoneNumber } : {}),
          ...(input.email ? { email: input.email } : {})
        }
      );
      response.json({ doctor });
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/doctors/:doctorId/pin-reset", async (request, response, next) => {
    try {
      const result = await authService.resetDoctorPin(request.params.doctorId);
      response.json(result);
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/doctors/:doctorId/pin-lock/clear", async (request, response, next) => {
    try {
      const doctor = await authService.clearDoctorPinLock(request.params.doctorId);
      response.json({ doctor });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
