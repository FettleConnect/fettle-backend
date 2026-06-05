import { Router } from "express";
import { z } from "zod";

import {
  acceptQueueEntryResponseSchema,
  listQueueEntriesResponseSchema,
  updateQueueEntryResponseSchema
} from "@medivoice/shared";

import { type AuthService } from "../auth-service.js";
import { type QueueService } from "../queue-service.js";

export function createInternalAdminQueueRouter({
  authService,
  queueService
}: {
  authService: AuthService;
  queueService: QueueService;
}): Router {
  const router = Router();

  router.get("/internal/admin/intake-links", async (request, response, next) => {
    try {
      const query = z.object({
        doctorId: z.string().uuid().optional()
      }).parse(request.query);
      const intakeLinks = await queueService.listIntakeLinksForAdmin(
        query.doctorId
      );
      response.json({ intakeLinks });
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/intake-links/:intakeLinkId", async (request, response, next) => {
    try {
      const intakeLink = await queueService.getIntakeLinkByIdForAdmin(
        request.params.intakeLinkId
      );
      response.json({ intakeLink });
    } catch (error) {
      next(error);
    }
  });

  router.patch("/internal/admin/intake-links/:intakeLinkId", async (request, response, next) => {
    try {
      const input = z.object({
        label: z.string().trim().min(1).optional(),
        isActive: z.boolean().optional()
      }).parse(request.body);
      const intakeLink = await queueService.updateIntakeLinkForAdmin(
        request.params.intakeLinkId,
        {
          ...(input.label ? { label: input.label } : {}),
          ...(typeof input.isActive === "boolean"
            ? { isActive: input.isActive }
            : {})
        }
      );
      response.json({ intakeLink });
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/queue", async (request, response, next) => {
    try {
      const query = z.object({
        doctorId: z.string().uuid().optional(),
        status: z.enum([
          "waiting",
          "accepted",
          "in_consultation",
          "completed",
          "cancelled",
          "no_show"
        ]).optional(),
        search: z.string().trim().min(1).optional()
      }).parse(request.query);
      const entries = await queueService.listQueueEntriesForAdmin({
        ...(query.doctorId ? { doctorId: query.doctorId } : {}),
        ...(query.status ? { status: query.status } : {}),
        ...(query.search ? { search: query.search } : {})
      });
      response.json(listQueueEntriesResponseSchema.parse({ entries }));
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/queue/:queueEntryId", async (request, response, next) => {
    try {
      const entry = await queueService.getQueueEntryByIdForAdmin(
        request.params.queueEntryId
      );
      response.json({ entry });
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/queue/:queueEntryId/accept", async (request, response, next) => {
    try {
      const entry = await queueService.getQueueEntryByIdForAdmin(
        request.params.queueEntryId
      );
      const doctor = await authService.getDoctorById(entry.doctorId);
      const result = await queueService.acceptQueueEntry(
        request.params.queueEntryId,
        doctor
      );
      response.status(201).json(acceptQueueEntryResponseSchema.parse(result));
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/queue/:queueEntryId/no-show", async (request, response, next) => {
    try {
      const queueEntry = await queueService.getQueueEntryByIdForAdmin(
        request.params.queueEntryId
      );
      const doctor = await authService.getDoctorById(queueEntry.doctorId);
      const entry = await queueService.markQueueEntryNoShow(
        request.params.queueEntryId,
        doctor
      );
      response.json(updateQueueEntryResponseSchema.parse({ entry }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/queue/:queueEntryId/cancel", async (request, response, next) => {
    try {
      const queueEntry = await queueService.getQueueEntryByIdForAdmin(
        request.params.queueEntryId
      );
      const doctor = await authService.getDoctorById(queueEntry.doctorId);
      const entry = await queueService.cancelQueueEntry(
        request.params.queueEntryId,
        doctor
      );
      response.json(updateQueueEntryResponseSchema.parse({ entry }));
    } catch (error) {
      next(error);
    }
  });

  return router;
}
