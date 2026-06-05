import { Router, type NextFunction, type Request, type Response } from "express";

import { logInfo, logWarn } from "../logger.js";
import { type TwilioRequestValidator } from "../twilio-request-validator.js";

type PdfDeliveryWebhookService = {
  handleProviderStatusUpdate?: (input: {
    providerMessageId: string;
    providerStatus?: string;
    errorMessage?: string;
  }) => Promise<unknown>;
  getPublicMediaAsset?: (token: string) => Promise<{
    sessionId: string;
    fileName: string;
    mimeType: string;
    data: Buffer;
  } | null>;
};

export function createPdfDeliveryRouter({
  pdfDeliveryService,
  twilioRequestValidator,
  twilioWebhookUrl,
  samplePdfBuffer
}: {
  pdfDeliveryService?: PdfDeliveryWebhookService;
  twilioRequestValidator?: TwilioRequestValidator;
  twilioWebhookUrl?: string;
  samplePdfBuffer: Buffer;
}): Router {
  const router = Router();

  router.post("/webhooks/twilio/whatsapp-status", async (request, response, next) => {
    try {
      if (!pdfDeliveryService?.handleProviderStatusUpdate) {
        logWarn("twilio_whatsapp_status_webhook_ignored", {
          reason: "pdf_delivery_disabled"
        }, { scope: "delivery" });
        response.sendStatus(204);
        return;
      }

      const body =
        typeof request.body === "object" && request.body !== null
          ? request.body
          : {};
      const messageSid =
        typeof body.MessageSid === "string" ? body.MessageSid.trim() : "";
      const webhookParams = Object.fromEntries(
        Object.entries(body).flatMap(([key, value]) =>
          typeof value === "string" ? [[key, value]] : []
        )
      );
      logInfo(
        "twilio_whatsapp_status_webhook_received",
        {
          messageSid,
          providerStatus:
            typeof body.MessageStatus === "string"
              ? body.MessageStatus.trim()
              : undefined,
          hasErrorMessage:
            typeof body.ErrorMessage === "string"
              ? body.ErrorMessage.trim().length > 0
              : false
        },
        { scope: "delivery" }
      );

      if (
        twilioRequestValidator &&
        twilioWebhookUrl &&
        !twilioRequestValidator.validate({
          signature: request.header("X-Twilio-Signature"),
          url: twilioWebhookUrl,
          params: webhookParams
        })
      ) {
        logWarn(
          "twilio_whatsapp_status_webhook_rejected",
          { reason: "invalid_signature", messageSid },
          { scope: "delivery" }
        );
        response.sendStatus(403);
        return;
      }

      if (!messageSid) {
        logWarn(
          "twilio_whatsapp_status_webhook_ignored",
          { reason: "missing_message_sid" },
          { scope: "delivery" }
        );
        response.sendStatus(204);
        return;
      }

      const updated = await pdfDeliveryService.handleProviderStatusUpdate({
        providerMessageId: messageSid,
        providerStatus:
          typeof body.MessageStatus === "string"
            ? body.MessageStatus.trim()
            : undefined,
        errorMessage:
          typeof body.ErrorMessage === "string"
            ? body.ErrorMessage.trim()
            : undefined
      });
      if (updated) {
        logInfo(
          "twilio_whatsapp_status_webhook_applied",
          { messageSid },
          { scope: "delivery" }
        );
      } else {
        logWarn(
          "twilio_whatsapp_status_webhook_unmatched",
          { messageSid },
          { scope: "delivery" }
        );
      }

      response.sendStatus(204);
    } catch (error) {
      next(error);
    }
  });

  const servePublicPdfDeliveryMedia = async (
    request: Request,
    response: Response,
    next: NextFunction
  ) => {
    try {
      const token =
        typeof request.query.token === "string" ? request.query.token.trim() : "";

      if (!token || !pdfDeliveryService?.getPublicMediaAsset) {
        response.sendStatus(404);
        return;
      }

      const asset = await pdfDeliveryService.getPublicMediaAsset(token);

      if (!asset) {
        logWarn(
          "pdf_delivery_public_media_denied",
          { reason: "invalid_or_expired_token" },
          { scope: "delivery" }
        );
        response.sendStatus(404);
        return;
      }

      response.setHeader("Content-Type", asset.mimeType);
      response.setHeader("Content-Length", String(asset.data.length));
      response.setHeader(
        "Content-Disposition",
        `inline; filename="${asset.fileName.replace(/"/g, "")}"`
      );
      response.setHeader("Cache-Control", "no-store");
      response.send(asset.data);
      logInfo(
        "pdf_delivery_public_media_served",
        {
          sessionId: asset.sessionId,
          mimeType: asset.mimeType,
          sizeBytes: asset.data.length
        },
        { scope: "delivery" }
      );
    } catch (error) {
      next(error);
    }
  };

  router.get("/public/pdf-delivery/template-sample.pdf", (_request, response) => {
    response.setHeader("Content-Type", "application/pdf");
    response.setHeader("Content-Length", String(samplePdfBuffer.length));
    response.setHeader(
      "Content-Disposition",
      'inline; filename="medivoice-template-sample.pdf"'
    );
    response.setHeader("Cache-Control", "public, max-age=300");
    response.send(samplePdfBuffer);
  });

  router.get("/public/pdf-delivery/media", servePublicPdfDeliveryMedia);
  router.get("/public/pdf-delivery/media/:fileName", servePublicPdfDeliveryMedia);

  return router;
}
