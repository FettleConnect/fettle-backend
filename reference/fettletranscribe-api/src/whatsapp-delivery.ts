import {
  type ConsultationSession,
  type DocumentArtifact,
  type PdfDeliveryJob
} from "@medivoice/shared";

import { type GeneratedDocumentArtifact } from "./document-service.js";
import { toIndianE164PhoneNumber } from "./phone-number.js";
import { type PdfDeliverySendResult } from "./pdf-delivery-service.js";

export class WhatsAppDeliveryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WhatsAppDeliveryError";
  }
}

export interface WhatsappDeliveryContext {
  job: PdfDeliveryJob;
  session: ConsultationSession;
  generatedDocument: GeneratedDocumentArtifact & { artifact: DocumentArtifact };
  mediaUrl: string;
}

export interface WhatsappDeliverySender {
  send(context: WhatsappDeliveryContext): Promise<PdfDeliverySendResult>;
}

interface TwilioWhatsappSenderOptions {
  accountSid: string;
  authToken: string;
  fromWhatsAppNumber: string;
  statusCallbackUrl: string;
  contentSid?: string;
  contentVariables?: Record<string, string>;
}

interface TwilioMessageResponse {
  sid?: string;
  status?: string;
  error_message?: string | null;
  message?: string;
}

export class TwilioWhatsappSender implements WhatsappDeliverySender {
  constructor(private readonly options: TwilioWhatsappSenderOptions) {}

  async send(context: WhatsappDeliveryContext): Promise<PdfDeliverySendResult> {
    const body = new URLSearchParams({
      To: toIndianWhatsappAddress(context.job.phoneNumber),
      From: toWhatsappAddress(this.options.fromWhatsAppNumber),
      StatusCallback: this.options.statusCallbackUrl
    });

    if (this.options.contentSid) {
      body.set("ContentSid", this.options.contentSid);
      body.set(
        "ContentVariables",
        JSON.stringify(buildTemplateVariables(context, this.options.contentVariables))
      );
    } else {
      body.set("Body", buildMessageBody(context));
      body.set("MediaUrl", context.mediaUrl);
    }

    const response = await fetch(
      `https://api.twilio.com/2010-04-01/Accounts/${this.options.accountSid}/Messages.json`,
      {
        method: "POST",
        headers: {
          Authorization: `Basic ${Buffer.from(
            `${this.options.accountSid}:${this.options.authToken}`
          ).toString("base64")}`,
          "Content-Type": "application/x-www-form-urlencoded"
        },
        body
      }
    );
    const payload = (await response.json()) as TwilioMessageResponse;

    if (!response.ok || !payload.sid) {
      throw new WhatsAppDeliveryError(
        payload.error_message?.trim() ||
          payload.message?.trim() ||
          "Unable to send the consultation PDF over WhatsApp."
      );
    }

    return {
      provider: "twilio-whatsapp",
      providerMessageId: payload.sid
    };
  }
}

export function mapTwilioMessageStatus(
  status: string | undefined
): "queued" | "sent" | "delivered" | "read" | "failed" {
  switch (status) {
    case "queued":
    case "accepted":
    case "scheduled":
    case "sending":
      return "queued";
    case "sent":
      return "sent";
    case "delivered":
      return "delivered";
    case "read":
      return "read";
    default:
      return "failed";
  }
}

function buildMessageBody(context: WhatsappDeliveryContext): string {
  const recipientLabel =
    context.job.recipientType === "patient"
      ? context.session.patient.name
      : context.job.recipientName;

  return (
    context.generatedDocument.artifact.delivery.whatsappMessage ||
    `Hello ${recipientLabel}, your digital prescription is attached. Reply if you need help.`
  );
}

function buildTemplateVariables(
  context: WhatsappDeliveryContext,
  configuredVariables?: Record<string, string>
): Record<string, string> {
  const recipientLabel =
    context.job.recipientType === "patient"
      ? context.session.patient.name
      : context.job.recipientName;

  const replacements: Record<string, string> = {
    recipientName: recipientLabel,
    patientName: context.session.patient.name,
    mediaUrl: context.mediaUrl,
    mediaPath: toTemplateMediaPath(context.mediaUrl),
    sessionId: context.session.id,
    recipientType: context.job.recipientType
  };

  if (!configuredVariables || Object.keys(configuredVariables).length === 0) {
    return {
      "1": recipientLabel,
      "2": context.mediaUrl
    };
  }

  const resolvedEntries = Object.entries(configuredVariables).map(([key, value]) => [
    key,
    value.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_match, token: string) =>
      replacements[token] ?? ""
    )
  ]);

  return Object.fromEntries(resolvedEntries);
}

function toTemplateMediaPath(mediaUrl: string): string {
  try {
    const parsed = new URL(mediaUrl);
    return `${parsed.pathname.replace(/^\//, "")}${parsed.search}`;
  } catch {
    return mediaUrl;
  }
}

function toWhatsappAddress(phoneNumber: string): string {
  return phoneNumber.startsWith("whatsapp:")
    ? phoneNumber
    : `whatsapp:${phoneNumber}`;
}

export function toIndianWhatsappAddress(phoneNumber: string): string {
  const normalizedValue = phoneNumber.startsWith("whatsapp:")
    ? phoneNumber.slice("whatsapp:".length)
    : phoneNumber;

  return `whatsapp:${toIndianE164PhoneNumber(normalizedValue)}`;
}
