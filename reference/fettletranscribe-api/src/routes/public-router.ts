import { Router } from "express";
import { z } from "zod";

import {
  getPatientProfileByPhoneResponseSchema,
  getPublicIntakeLinkResponseSchema,
  submitPublicIntakeResponseSchema
} from "@medivoice/shared";

import { type AuthService } from "../auth-service.js";
import { type QueueService } from "../queue-service.js";

export function createPublicRouter({
  authService,
  queueService
}: {
  authService: AuthService;
  queueService: QueueService;
}): Router {
  const router = Router();

  router.get("/public/intake/:token", async (request, response, next) => {
    try {
      const intakeLink = await queueService.getPublicIntakeLink(
        request.params.token
      );
      response.json(
        getPublicIntakeLinkResponseSchema.parse({
          intakeLink
        })
      );
    } catch (error) {
      next(error);
    }
  });

  router.get("/public/intake/:token/patient-profile", async (request, response, next) => {
    try {
      const query = z.object({ phone: z.string().trim().regex(/^\d{10}$/) }).parse(request.query);
      const profile = await queueService.getPublicPatientProfileByPhone(
        request.params.token,
        query.phone
      );
      response.json(
        getPatientProfileByPhoneResponseSchema.parse({
          found: Boolean(profile),
          ...(profile ? { profile } : {})
        })
      );
    } catch (error) {
      next(error);
    }
  });

  router.get(
    "/public/intake/by-slug/:departmentSlug/:doctorNameSlug",
    async (request, response, next) => {
      try {
        const doctor = await authService.getDoctorByPublicPath(
          request.params.departmentSlug,
          request.params.doctorNameSlug
        );
        const intakeLink = await queueService.getDoctorIntakeLink(doctor);
        const publicIntakeLink = await queueService.getPublicIntakeLink(
          intakeLink.token
        );
        response.json(
          getPublicIntakeLinkResponseSchema.parse({
            intakeLink: {
              id: publicIntakeLink.id,
              doctorName: publicIntakeLink.doctorName,
              label: publicIntakeLink.label,
              token: publicIntakeLink.token,
              isActive: publicIntakeLink.isActive
            }
          })
        );
      } catch (error) {
        next(error);
      }
    }
  );

  router.post("/public/intake/:token", async (request, response, next) => {
    try {
      const entry = await queueService.submitPublicIntake(
        request.params.token,
        request.body
      );
      response
        .status(201)
        .json(submitPublicIntakeResponseSchema.parse({ entry }));
    } catch (error) {
      next(error);
    }
  });

  return router;
}
