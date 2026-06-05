import { describe, expect, it } from "vitest";

import {
  mapTwilioMessageStatus,
  TwilioWhatsappSender,
  toIndianWhatsappAddress
} from "./whatsapp-delivery.js";

describe("mapTwilioMessageStatus", () => {
  it("maps accepted and queued provider states to queued", () => {
    expect(mapTwilioMessageStatus("accepted")).toBe("queued");
    expect(mapTwilioMessageStatus("queued")).toBe("queued");
    expect(mapTwilioMessageStatus("sending")).toBe("queued");
  });

  it("maps downstream delivery states directly", () => {
    expect(mapTwilioMessageStatus("sent")).toBe("sent");
    expect(mapTwilioMessageStatus("delivered")).toBe("delivered");
    expect(mapTwilioMessageStatus("read")).toBe("read");
  });

  it("falls back unknown provider states to failed", () => {
    expect(mapTwilioMessageStatus("undelivered")).toBe("failed");
    expect(mapTwilioMessageStatus(undefined)).toBe("failed");
  });
});

describe("toIndianWhatsappAddress", () => {
  it("normalizes local Indian numbers to whatsapp +91 format", () => {
    expect(toIndianWhatsappAddress("9945437983")).toBe("whatsapp:+919945437983");
  });

  it("accepts whatsapp-prefixed numbers and still normalizes to +91", () => {
    expect(toIndianWhatsappAddress("whatsapp:9945437983")).toBe(
      "whatsapp:+919945437983"
    );
  });
});

describe("TwilioWhatsappSender", () => {
  it("uses ContentSid and ContentVariables when template mode is configured", async () => {
    const sender = new TwilioWhatsappSender({
      accountSid: "AC123",
      authToken: "token",
      fromWhatsAppNumber: "+15559509116",
      statusCallbackUrl: "https://example.com/api/webhooks/twilio/whatsapp-status",
      contentSid: "HX_TEMPLATE_123",
      contentVariables: {
        "1": "{{mediaPath}}",
        "2": "{{patientName}}"
      }
    });

    let postedBody: URLSearchParams | null = null;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (_input, init) => {
      postedBody =
        init?.body instanceof URLSearchParams
          ? init.body
          : new URLSearchParams(String(init?.body ?? ""));
      return new Response(JSON.stringify({ sid: "MM123" }), { status: 201 });
    };

    try {
      const context = {
        job: {
          id: "job-1",
          sessionId: "session-1",
          recipientType: "patient",
          recipientName: "Patient Name",
          phoneNumber: "9945437983",
          status: "pending",
          attemptCount: 0,
          createdAt: "2026-04-08T00:00:00.000Z",
          updatedAt: "2026-04-08T00:00:00.000Z"
        },
        session: {
          id: "session-1",
          patient: {
            name: "Atulya Rai",
            phoneNumber: "9945437983"
          }
        },
        generatedDocument: {
          pdf: Buffer.from("pdf"),
          artifact: {
            fileName: "consultation.pdf",
            mimeType: "application/pdf",
            sizeBytes: 3,
            generatedAt: "2026-04-08T00:00:00.000Z",
            delivery: {
              whatsappMessage: "fallback"
            }
          }
        },
        mediaUrl: "https://example.com/public/pdf-delivery/media?token=abc"
      } as unknown as Parameters<TwilioWhatsappSender["send"]>[0];

      await sender.send(context);
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(postedBody).not.toBeNull();
    const body = postedBody ?? new URLSearchParams();
    expect(body.get("ContentSid")).toBe("HX_TEMPLATE_123");
    expect(body.get("Body")).toBeNull();
    expect(body.get("MediaUrl")).toBeNull();
    expect(JSON.parse(body.get("ContentVariables") ?? "{}")).toEqual({
      "1": "public/pdf-delivery/media?token=abc",
      "2": "Atulya Rai"
    });
  });

  it("falls back to freeform Body + MediaUrl when template mode is not configured", async () => {
    const sender = new TwilioWhatsappSender({
      accountSid: "AC123",
      authToken: "token",
      fromWhatsAppNumber: "+15559509116",
      statusCallbackUrl: "https://example.com/api/webhooks/twilio/whatsapp-status"
    });

    let postedBody: URLSearchParams | null = null;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (_input, init) => {
      postedBody =
        init?.body instanceof URLSearchParams
          ? init.body
          : new URLSearchParams(String(init?.body ?? ""));
      return new Response(JSON.stringify({ sid: "MM123" }), { status: 201 });
    };

    try {
      const context = {
        job: {
          id: "job-2",
          sessionId: "session-2",
          recipientType: "patient",
          recipientName: "Patient Name",
          phoneNumber: "9945437983",
          status: "pending",
          attemptCount: 0,
          createdAt: "2026-04-08T00:00:00.000Z",
          updatedAt: "2026-04-08T00:00:00.000Z"
        },
        session: {
          id: "session-2",
          patient: {
            name: "Atulya Rai",
            phoneNumber: "9945437983"
          }
        },
        generatedDocument: {
          pdf: Buffer.from("pdf"),
          artifact: {
            fileName: "consultation.pdf",
            mimeType: "application/pdf",
            sizeBytes: 3,
            generatedAt: "2026-04-08T00:00:00.000Z",
            delivery: {
              whatsappMessage: "Custom body"
            }
          }
        },
        mediaUrl: "https://example.com/public/pdf-delivery/media?token=def"
      } as unknown as Parameters<TwilioWhatsappSender["send"]>[0];

      await sender.send(context);
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(postedBody).not.toBeNull();
    const body = postedBody ?? new URLSearchParams();
    expect(body.get("Body")).toBe("Custom body");
    expect(body.get("MediaUrl")).toBe(
      "https://example.com/public/pdf-delivery/media?token=def"
    );
    expect(body.get("ContentSid")).toBeNull();
  });

  it("uses the patient prescription message when no custom body is stored", async () => {
    const sender = new TwilioWhatsappSender({
      accountSid: "AC123",
      authToken: "token",
      fromWhatsAppNumber: "+15559509116",
      statusCallbackUrl: "https://example.com/api/webhooks/twilio/whatsapp-status"
    });

    let postedBody: URLSearchParams | null = null;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (_input, init) => {
      postedBody =
        init?.body instanceof URLSearchParams
          ? init.body
          : new URLSearchParams(String(init?.body ?? ""));
      return new Response(JSON.stringify({ sid: "MM123" }), { status: 201 });
    };

    try {
      const context = {
        job: {
          id: "job-3",
          sessionId: "session-3",
          recipientType: "patient",
          recipientName: "Patient Name",
          phoneNumber: "9945437983",
          status: "pending",
          attemptCount: 0,
          createdAt: "2026-04-08T00:00:00.000Z",
          updatedAt: "2026-04-08T00:00:00.000Z"
        },
        session: {
          id: "session-3",
          patient: {
            name: "Atulya Rai",
            phoneNumber: "9945437983"
          }
        },
        generatedDocument: {
          pdf: Buffer.from("pdf"),
          artifact: {
            fileName: "consultation.pdf",
            mimeType: "application/pdf",
            sizeBytes: 3,
            generatedAt: "2026-04-08T00:00:00.000Z",
            delivery: {
              whatsappMessage: ""
            }
          }
        },
        mediaUrl: "https://example.com/public/pdf-delivery/media?token=ghi"
      } as unknown as Parameters<TwilioWhatsappSender["send"]>[0];

      await sender.send(context);
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(postedBody).not.toBeNull();
    const body = postedBody ?? new URLSearchParams();
    expect(body.get("Body")).toBe(
      "Hello Atulya Rai, your digital prescription is attached. Reply if you need help."
    );
  });
});
