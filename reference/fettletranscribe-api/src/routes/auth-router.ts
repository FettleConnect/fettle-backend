import { Router, type Request } from "express";

import {
  authMeResponseSchema,
  listDoctorsQuerySchema,
  listDoctorsResponseSchema,
  loginDoctorResponseSchema,
  pinChangeRequestSchema,
  pinLoginRequestSchema,
  pinLoginResponseSchema,
  selectDoctorSessionRequestSchema
} from "@medivoice/shared";

import { type AuthService } from "../auth-service.js";
import { type requireDoctor as requireDoctorFn } from "../http-helpers.js";

export function createAuthRouter({
  authService,
  emitFlowMetric,
  requireDoctor
}: {
  authService: AuthService;
  emitFlowMetric: (
    metric: string,
    startedAt: number,
    status: "success" | "failure",
    context?: Record<string, string | number | boolean>
  ) => void;
  requireDoctor: (request: Request) => ReturnType<typeof requireDoctorFn>;
}): Router {
  const router = Router();

  router.get("/doctors", async (request, response, next) => {
    try {
      const { search } = listDoctorsQuerySchema.parse(request.query);
      const doctors = await authService.listDoctors(search);
      response.json(listDoctorsResponseSchema.parse({ doctors }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/auth/select", async (request, response, next) => {
    const startedAt = Date.now();
    try {
      const input = selectDoctorSessionRequestSchema.parse(request.body);
      const result = await authService.loginWithDoctorId(input.doctorId);
      emitFlowMetric("auth.select", startedAt, "success", {
        doctorId: input.doctorId
      });
      response.json(
        loginDoctorResponseSchema.parse({
          token: result.token,
          doctor: result.doctor
        })
      );
    } catch (error) {
      emitFlowMetric("auth.select", startedAt, "failure");
      next(error);
    }
  });

  router.post("/auth/pin/login", async (request, response, next) => {
    const startedAt = Date.now();
    try {
      const input = pinLoginRequestSchema.parse(request.body);
      const result = await authService.loginWithPin(input.doctorId, input.pin);
      emitFlowMetric("auth.pin_login", startedAt, "success", {
        doctorId: input.doctorId,
        status: result.status
      });
      response.json(pinLoginResponseSchema.parse(result));
    } catch (error) {
      emitFlowMetric("auth.pin_login", startedAt, "failure");
      next(error);
    }
  });

  router.post("/auth/pin/change", async (request, response, next) => {
    const startedAt = Date.now();
    try {
      const input = pinChangeRequestSchema.parse(request.body);
      const result = await authService.changePin(
        input.pinChangeToken,
        input.newPin
      );
      emitFlowMetric("auth.pin_change", startedAt, "success");
      response.json(loginDoctorResponseSchema.parse(result));
    } catch (error) {
      emitFlowMetric("auth.pin_change", startedAt, "failure");
      next(error);
    }
  });

  router.get("/auth/me", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      response.json(authMeResponseSchema.parse({ doctor }));
    } catch (error) {
      next(error);
    }
  });

  return router;
}
