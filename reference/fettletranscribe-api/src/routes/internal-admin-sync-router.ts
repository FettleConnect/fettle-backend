import { Router } from "express";
import { z } from "zod";

import { FeatureNotReadyError, NotFoundError } from "../errors.js";
import { type FettleSyncService } from "../fettle-sync-service.js";

export function createInternalAdminSyncRouter({
  fettleSyncService
}: {
  fettleSyncService?: FettleSyncService;
}): Router {
  const router = Router();

  router.get("/internal/admin/fettle-sync/stats", async (_request, response, next) => {
    try {
      if (!fettleSyncService) {
        response.json({
          enabled: false,
          queued: 0,
          processing: 0,
          succeeded: 0,
          deadLetter: 0,
          retryable: 0
        });
        return;
      }
      response.json(await fettleSyncService.getStats());
    } catch (error) {
      next(error);
    }
  });

  router.get("/internal/admin/fettle-sync/jobs", async (request, response, next) => {
    try {
      if (!fettleSyncService) {
        response.json({ jobs: [] });
        return;
      }
      const query = z.object({
        status: z.enum(["queued", "processing", "succeeded", "dead_letter"]).optional(),
        limit: z.coerce.number().int().min(1).max(200).optional()
      }).parse(request.query);
      response.json({
        jobs: await fettleSyncService.listJobs({
          ...(query.status ? { status: query.status } : {}),
          ...(query.limit ? { limit: query.limit } : {})
        })
      });
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/fettle-sync/dry-run", async (_request, response, next) => {
    try {
      if (!fettleSyncService) {
        throw new FeatureNotReadyError("Fettle sync is not configured.");
      }
      const job = await fettleSyncService.enqueueDryRun();
      const result = await fettleSyncService.processDueJobs(1);
      response.status(202).json({ job, result });
    } catch (error) {
      next(error);
    }
  });

  router.post("/internal/admin/fettle-sync/jobs/:jobId/replay", async (request, response, next) => {
    try {
      if (!fettleSyncService) {
        throw new FeatureNotReadyError("Fettle sync is not configured.");
      }
      const job = await fettleSyncService.replayDeadLetter(request.params.jobId);
      if (!job) {
        throw new NotFoundError("Dead-letter sync job was not found.");
      }
      response.json({
        job: {
          id: job.id,
          sessionId: job.sessionId,
          trigger: job.trigger,
          status: job.status,
          attempts: job.attempts,
          maxAttempts: job.maxAttempts,
          firstQueuedAt: job.firstQueuedAt,
          nextAttemptAt: job.nextAttemptAt,
          updatedAt: job.updatedAt,
          ...(job.lastError ? { lastError: job.lastError } : {}),
          ...(job.lastErrorClass ? { lastErrorClass: job.lastErrorClass } : {}),
          ...(job.finishedAt ? { finishedAt: job.finishedAt } : {})
        }
      });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
