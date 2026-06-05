import { Router, type Request } from "express";

import {
  acceptQueueEntryResponseSchema,
  getDoctorIntakeLinkResponseSchema,
  listQueueEntriesResponseSchema,
  updateQueueEntryResponseSchema
} from "@medivoice/shared";

import { type requireDoctor as requireDoctorFn } from "../http-helpers.js";
import { type QueueService } from "../queue-service.js";

export function createQueueRouter({
  queueService,
  requireDoctor
}: {
  queueService: QueueService;
  requireDoctor: (request: Request) => ReturnType<typeof requireDoctorFn>;
}): Router {
  const router = Router();

  router.get("/intake-link", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const intakeLink = await queueService.getDoctorIntakeLink(doctor);
      response.json(
        getDoctorIntakeLinkResponseSchema.parse({
          intakeLink
        })
      );
    } catch (error) {
      next(error);
    }
  });

  router.get("/queue", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const entries = await queueService.listQueueEntries(doctor.id);
      response.json(listQueueEntriesResponseSchema.parse({ entries }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/queue/:queueEntryId/accept", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const result = await queueService.acceptQueueEntry(
        request.params.queueEntryId,
        doctor
      );
      response.status(201).json(acceptQueueEntryResponseSchema.parse(result));
    } catch (error) {
      next(error);
    }
  });

  router.post("/queue/:queueEntryId/no-show", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
      const entry = await queueService.markQueueEntryNoShow(
        request.params.queueEntryId,
        doctor
      );
      response.json(updateQueueEntryResponseSchema.parse({ entry }));
    } catch (error) {
      next(error);
    }
  });

  router.post("/queue/:queueEntryId/cancel", async (request, response, next) => {
    try {
      const doctor = await requireDoctor(request);
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
