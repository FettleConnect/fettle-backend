import { describe, expect, it, vi } from "vitest";
import { PDFDocument } from "pdf-lib";

import { AIProviderError } from "./errors.js";
import {
  FallbackReportContextAdapter,
  GeminiReportContextAdapter,
  OpenAIReportContextAdapter
} from "./report-context-service.js";

async function createPdfBuffer(pageCount: number): Promise<Buffer> {
  const pdf = await PDFDocument.create();
  for (let index = 0; index < pageCount; index += 1) {
    pdf.addPage([200, 200]);
  }
  return Buffer.from(await pdf.save());
}

describe("GeminiReportContextAdapter", () => {
  it("reports malformed chunk payloads as provider failures", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: [
                        {
                          title: "Lab result",
                          category: "Lab"
                        }
                      ]
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      )
    );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    await expect(
      adapter.extractChunks({
        sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
        uploads: [
          {
            id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
            sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
            fileName: "lab-report.pdf",
            mimeType: "application/pdf",
            sizeBytes: 12,
            data: Buffer.from("%PDF report"),
            expiresAt: new Date(Date.now() + 60_000).toISOString(),
            uploadedAt: new Date().toISOString()
          }
        ]
      })
    ).rejects.toThrow("REPORT_CONTEXT_NO_EXTRACTABLE_CONTENT");
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("accepts wrapped provider payload and normalizes alternate chunk keys", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      data: {
                        chunks: [
                          {
                            heading: "HbA1c trend",
                            type: "Lab",
                            page: "2",
                            filename: "/tmp/Lab-Report.PDF",
                            content: "HbA1c is elevated."
                          }
                        ]
                      }
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      )
    );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "lab-report.pdf",
          mimeType: "application/pdf",
          sizeBytes: 12,
          data: Buffer.from("%PDF report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.sourceFileName).toBe("lab-report.pdf");
    expect(chunks[0]?.title).toBe("HbA1c trend");
    expect(chunks[0]?.category).toBe("Lab");
    expect(chunks[0]?.pageNumber).toBe(2);
  });

  it("falls back to the only uploaded file when provider omits a matching filename", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: [
                        {
                          title: "ECG review",
                          category: "Cardiology",
                          sourceFileName: "unknown-source.pdf",
                          text: "No acute ischemic changes."
                        }
                      ]
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      )
    );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "visit-summary.jpg",
          mimeType: "image/jpeg",
          sizeBytes: 12,
          data: Buffer.from("jpeg-report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.sourceFileName).toBe("visit-summary.jpg");
  });

  it("accepts empty extracted chunks without throwing provider error", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: []
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      )
    );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "visit-summary.jpg",
          mimeType: "image/jpeg",
          sizeBytes: 12,
          data: Buffer.from("jpeg-report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(chunks).toEqual([]);
  });

  it("normalizes vague categories using title and text hints", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: [
                        {
                          title: "CT Brain Findings",
                          category: "Report",
                          sourceFileName: "visit-summary.jpg",
                          text: "CT scan shows no acute intracranial bleed."
                        }
                      ]
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      )
    );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "visit-summary.jpg",
          mimeType: "image/jpeg",
          sizeBytes: 12,
          data: Buffer.from("jpeg-report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.category).toBe("Imaging");
  });

  it("deduplicates repeated chunk findings from provider output", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: [
                        {
                          title: "TSH",
                          category: "Lab",
                          sourceFileName: "lab-report.pdf",
                          text: "TSH elevated at 5.07 IU/mL."
                        },
                        {
                          title: "TSH",
                          category: "General",
                          sourceFileName: "lab-report.pdf",
                          text: "TSH elevated at 5.07 IU/mL."
                        }
                      ]
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      )
    );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "lab-report.pdf",
          mimeType: "application/pdf",
          sizeBytes: 12,
          data: Buffer.from("%PDF report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.category).toBe("Lab");
  });

  it("retries transient provider failures for extractChunks", async () => {
    let attempts = 0;
    const fetchImpl = vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("temporary network failure");
      }

      return new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: [
                        {
                          title: "CBC",
                          category: "Lab",
                          sourceFileName: "lab-report.pdf",
                          text: "WBC mildly elevated."
                        }
                      ]
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      );
    });
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "lab-report.pdf",
          mimeType: "application/pdf",
          sizeBytes: 12,
          data: Buffer.from("%PDF report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(attempts).toBe(2);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.title).toBe("CBC");
  });

  it("splits multi-page PDFs into bounded parallel extraction units", async () => {
    const pdfBuffer = await createPdfBuffer(3);
    let activeCalls = 0;
    let maxActiveCalls = 0;
    const seenPages: number[] = [];
    const fetchImpl = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      activeCalls += 1;
      maxActiveCalls = Math.max(maxActiveCalls, activeCalls);
      await new Promise((resolve) => setTimeout(resolve, 10));
      activeCalls -= 1;

      const requestBody = JSON.parse(String(init?.body)) as {
        contents: Array<{ parts: Array<{ text?: string }> }>;
      };
      const pageText = requestBody.contents[0]?.parts
        .map((part) => part.text ?? "")
        .find((text) => text.includes("PDF page:"));
      const pageNumber = Number(/PDF page:\s*(\d+)/.exec(pageText ?? "")?.[1] ?? 0);
      seenPages.push(pageNumber);

      return new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: [
                        {
                          title: `Page ${pageNumber} finding`,
                          category: "Lab",
                          sourceFileName: "multi-page.pdf",
                          text: `Finding from page ${pageNumber}.`
                        }
                      ]
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      );
    });
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl,
      concurrency: 2,
      maxUnits: 10
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "multi-page.pdf",
          mimeType: "application/pdf",
          sizeBytes: pdfBuffer.byteLength,
          data: pdfBuffer,
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(maxActiveCalls).toBeLessThanOrEqual(2);
    expect(seenPages.sort()).toEqual([1, 2, 3]);
    expect(chunks.map((chunk) => chunk.pageNumber).sort()).toEqual([1, 2, 3]);
  });

  it("rejects PDFs that expand beyond the configured extraction unit cap", async () => {
    const pdfBuffer = await createPdfBuffer(3);
    const fetchImpl = vi.fn();
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl,
      maxUnits: 2
    });

    await expect(
      adapter.extractChunks({
        sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
        uploads: [
          {
            id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
            sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
            fileName: "too-long.pdf",
            mimeType: "application/pdf",
            sizeBytes: pdfBuffer.byteLength,
            data: pdfBuffer,
            expiresAt: new Date(Date.now() + 60_000).toISOString(),
            uploadedAt: new Date().toISOString()
          }
        ]
      })
    ).rejects.toThrow("REPORT_CONTEXT_TOO_MANY_PAGES");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("salvages valid chunks after retry parse also fails", async () => {
    let attempts = 0;
    const fetchImpl = vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) {
        return new Response(
          JSON.stringify({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        chunks: [
                          { title: "Bad chunk", category: "Lab" }
                        ]
                      })
                    }
                  ]
                }
              }
            ]
          }),
          { status: 200 }
        );
      }
      return new Response(
        JSON.stringify({
          candidates: [
            {
              content: {
                parts: [
                  {
                    text: JSON.stringify({
                      chunks: [
                        {
                          title: "CBC",
                          category: "Lab",
                          sourceFileName: "lab-report.pdf",
                          text: "WBC mildly elevated."
                        },
                        {
                          title: "broken",
                          category: "Lab"
                        }
                      ]
                    })
                  }
                ]
              }
            }
          ]
        }),
        { status: 200 }
      );
    });

    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "lab-report.pdf",
          mimeType: "application/pdf",
          sizeBytes: 12,
          data: Buffer.from("%PDF report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.title).toBe("CBC");
  });

  it("classifies provider timeout failures with explicit code", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("report_context:gemini_generate timed out after 20000ms.");
    });
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    await expect(
      adapter.extractChunks({
        sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
        uploads: [
          {
            id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
            sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
            fileName: "lab-report.pdf",
            mimeType: "application/pdf",
            sizeBytes: 12,
            data: Buffer.from("%PDF report"),
            expiresAt: new Date(Date.now() + 60_000).toISOString(),
            uploadedAt: new Date().toISOString()
          }
        ]
      })
    ).rejects.toThrow("REPORT_CONTEXT_PROVIDER_TIMEOUT");
  });

  it("retries retryable provider statuses before suggesting draft updates", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response("Service unavailable", { status: 503 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        draftDocument: {
                          title: "Follow-up note",
                          chiefComplaint: "Cough",
                          illnessHistory: "Report reviewed.",
                          clinicalSummary: "Stable.",
                          medications: [],
                          testsPrescribed: [],
                          nextSteps: ["Review report"],
                          cautions: []
                        },
                        changedFields: ["illnessHistory"]
                      })
                    }
                  ]
                }
              }
            ]
          }),
          { status: 200 }
        )
      );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl,
      maxRetries: 1,
      retryDelayMs: 1
    });

    const suggestion = await adapter.suggestDraft({
      currentDraft: {
        title: "Follow-up note",
        chiefComplaint: "Cough",
        illnessHistory: "Cough for two days.",
        clinicalSummary: "",
        medications: [],
        testsPrescribed: [],
        revisitDate: "",
        nextSteps: [],
        cautions: []
      },
      selectedChunks: [
        {
          id: "e99d9205-e17b-4728-ae6d-f66b6f839ac6",
          title: "CBC",
          category: "Lab",
          sourceFileName: "lab-report.pdf",
          text: "WBC mildly elevated.",
          extractedAt: new Date().toISOString()
        }
      ]
    });

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(suggestion.changedFields).toEqual(["illnessHistory"]);
  });

  it("retries suggestDraft once when first payload is schema-mismatched", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        draft: {
                          illnessHistory: "Wrong shape"
                        }
                      })
                    }
                  ]
                }
              }
            ]
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        draftDocument: {
                          title: "Follow-up note",
                          chiefComplaint: "Cough",
                          illnessHistory: "Report reviewed.",
                          clinicalSummary: "Stable.",
                          medications: [],
                          testsPrescribed: [],
                          nextSteps: ["Review report"],
                          cautions: []
                        },
                        changedFields: ["illnessHistory"]
                      })
                    }
                  ]
                }
              }
            ]
          }),
          { status: 200 }
        )
      );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const suggestion = await adapter.suggestDraft({
      currentDraft: {
        title: "Follow-up note",
        chiefComplaint: "Cough",
        illnessHistory: "Cough for two days.",
        clinicalSummary: "",
        medications: [],
        testsPrescribed: [],
        revisitDate: "",
        nextSteps: [],
        cautions: []
      },
      selectedChunks: []
    });

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(suggestion.changedFields).toEqual(["illnessHistory"]);
  });

  it("does not retry non-retryable provider statuses", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response("Bad request", { status: 400 })
    );
    const adapter = new GeminiReportContextAdapter({
      apiKey: "test-key",
      fetchImpl,
      maxRetries: 2,
      retryDelayMs: 1
    });

    await expect(
      adapter.suggestDraft({
        currentDraft: {
          title: "Follow-up note",
          chiefComplaint: "Cough",
          illnessHistory: "Cough for two days.",
          clinicalSummary: "",
          medications: [],
          testsPrescribed: [],
          revisitDate: "",
          nextSteps: [],
          cautions: []
        },
        selectedChunks: [
          {
            id: "e99d9205-e17b-4728-ae6d-f66b6f839ac6",
            title: "CBC",
            category: "Lab",
            sourceFileName: "lab-report.pdf",
            text: "WBC mildly elevated.",
            extractedAt: new Date().toISOString()
          }
        ]
      })
    ).rejects.toThrow("REPORT_CONTEXT_PROVIDER_HTTP_ERROR");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});

describe("OpenAIReportContextAdapter", () => {
  it("parses response output_text for extracted chunks", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({
          output_text: JSON.stringify({
            chunks: [
              {
                title: "CBC",
                category: "Lab",
                sourceFileName: "lab-report.pdf",
                text: "WBC mildly elevated."
              }
            ]
          })
        }),
        { status: 200 }
      )
    );
    const adapter = new OpenAIReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const chunks = await adapter.extractChunks({
      sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
      uploads: [
        {
          id: "4e262e3f-84f7-4902-bd7d-647a789c745c",
          sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
          fileName: "lab-report.pdf",
          mimeType: "application/pdf",
          sizeBytes: 12,
          data: Buffer.from("%PDF report"),
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          uploadedAt: new Date().toISOString()
        }
      ]
    });

    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.title).toBe("CBC");
  });

  it("retries suggestDraft once when first payload is schema-mismatched", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            output_text: JSON.stringify({
              draft: {
                illnessHistory: "Wrong shape"
              }
            })
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            output_text: JSON.stringify({
              draftDocument: {
                title: "Follow-up note",
                chiefComplaint: "Cough",
                illnessHistory: "Report reviewed.",
                clinicalSummary: "Stable.",
                medications: [],
                testsPrescribed: [],
                nextSteps: ["Review report"],
                cautions: []
              },
              changedFields: ["illnessHistory"]
            })
          }),
          { status: 200 }
        )
      );
    const adapter = new OpenAIReportContextAdapter({
      apiKey: "test-key",
      fetchImpl
    });

    const result = await adapter.suggestDraft({
      currentDraft: {
        title: "Follow-up note",
        chiefComplaint: "Cough",
        illnessHistory: "Cough for two days.",
        clinicalSummary: "",
        medications: [],
        testsPrescribed: [],
        revisitDate: "",
        nextSteps: [],
        cautions: []
      },
      selectedChunks: []
    });

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(result.changedFields).toEqual(["illnessHistory"]);
  });
});

describe("FallbackReportContextAdapter", () => {
  it("falls back for extractChunks on retryable schema mismatch", async () => {
    const fallbackExtract = vi.fn(async () => [
      {
        id: "2d307875-3d3d-4329-8868-7fcc33e4c2c6",
        sourceFileName: "lab-report.pdf",
        title: "CBC",
        category: "Lab",
        text: "Hemoglobin 12.2 g/dL.",
        extractedAt: new Date().toISOString()
      }
    ]);
    const adapter = new FallbackReportContextAdapter({
      primary: {
        providerName: "primary",
        async extractChunks() {
          throw new AIProviderError(
            "REPORT_CONTEXT_PROVIDER_SCHEMA_MISMATCH: provider returned an unexpected format."
          );
        },
        async suggestDraft() {
          throw new AIProviderError(
            "REPORT_CONTEXT_PROVIDER_HTTP_ERROR: provider returned a failed status (503)."
          );
        }
      },
      fallback: {
        providerName: "fallback",
        extractChunks: fallbackExtract,
        async suggestDraft() {
          return {
            draftDocument: {
              title: "Follow-up note",
              chiefComplaint: "Cough",
              illnessHistory: "Fallback worked.",
              clinicalSummary: "",
              medications: [],
              testsPrescribed: [],
              revisitDate: "",
              nextSteps: [],
              cautions: []
            },
            changedFields: ["illnessHistory"]
          };
        }
      }
    });

    await expect(
      adapter.extractChunks({
        sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
        uploads: []
      })
    ).resolves.toHaveLength(1);
    expect(fallbackExtract).toHaveBeenCalledOnce();
  });

  it("does not fall back for non-retryable extractChunks primary errors", async () => {
    const fallbackExtract = vi.fn(async () => []);
    const adapter = new FallbackReportContextAdapter({
      primary: {
        providerName: "primary",
        async extractChunks() {
          throw new AIProviderError(
            "REPORT_CONTEXT_TOO_MANY_PAGES: report includes 120 pages/files but the limit is 100."
          );
        },
        async suggestDraft() {
          throw new AIProviderError(
            "REPORT_CONTEXT_PROVIDER_HTTP_ERROR: provider returned a failed status (400)."
          );
        }
      },
      fallback: {
        providerName: "fallback",
        extractChunks: fallbackExtract,
        async suggestDraft() {
          return {
            draftDocument: {
              title: "Follow-up note",
              chiefComplaint: "Cough",
              illnessHistory: "Fallback worked.",
              clinicalSummary: "",
              medications: [],
              testsPrescribed: [],
              revisitDate: "",
              nextSteps: [],
              cautions: []
            },
            changedFields: ["illnessHistory"]
          };
        }
      }
    });

    await expect(
      adapter.extractChunks({
        sessionId: "78a0d65a-41e7-48bd-a2dd-c21e22044824",
        uploads: []
      })
    ).rejects.toThrow("REPORT_CONTEXT_TOO_MANY_PAGES");
    expect(fallbackExtract).not.toHaveBeenCalled();
  });

  it("falls back when primary fails with retryable status", async () => {
    const primary = {
      providerName: "primary",
      async extractChunks() {
        throw new AIProviderError(
          "REPORT_CONTEXT_PROVIDER_HTTP_ERROR: provider returned a failed status (503)."
        );
      },
      async suggestDraft() {
        throw new AIProviderError(
          "REPORT_CONTEXT_PROVIDER_HTTP_ERROR: provider returned a failed status (503)."
        );
      }
    };
    const fallback = {
      providerName: "fallback",
      async extractChunks() {
        return [];
      },
      async suggestDraft() {
        return {
          draftDocument: {
            title: "Follow-up note",
            chiefComplaint: "Cough",
            illnessHistory: "Fallback worked.",
            clinicalSummary: "",
            medications: [],
            testsPrescribed: [],
            revisitDate: "",
            nextSteps: [],
            cautions: []
          },
          changedFields: ["illnessHistory"]
        };
      }
    };
    const adapter = new FallbackReportContextAdapter({
      primary,
      fallback
    });

    await expect(
      adapter.suggestDraft({
        currentDraft: {
          title: "Follow-up note",
          chiefComplaint: "Cough",
          illnessHistory: "Cough for two days.",
          clinicalSummary: "",
          medications: [],
          testsPrescribed: [],
          revisitDate: "",
          nextSteps: [],
          cautions: []
        },
        selectedChunks: []
      })
    ).resolves.toMatchObject({ changedFields: ["illnessHistory"] });
  });

  it("does not fall back for non-retryable primary errors", async () => {
    const fallbackSuggest = vi.fn(async () => ({
      draftDocument: {
        title: "Follow-up note",
        chiefComplaint: "Cough",
        illnessHistory: "Fallback worked.",
        clinicalSummary: "",
        medications: [],
        testsPrescribed: [],
        revisitDate: "",
        nextSteps: [],
        cautions: []
      },
      changedFields: ["illnessHistory"]
    }));
    const adapter = new FallbackReportContextAdapter({
      primary: {
        providerName: "primary",
        async extractChunks() {
          throw new AIProviderError(
            "REPORT_CONTEXT_PROVIDER_HTTP_ERROR: provider returned a failed status (400)."
          );
        },
        async suggestDraft() {
          throw new AIProviderError(
            "REPORT_CONTEXT_PROVIDER_HTTP_ERROR: provider returned a failed status (400)."
          );
        }
      },
      fallback: {
        providerName: "fallback",
        async extractChunks() {
          return [];
        },
        suggestDraft: fallbackSuggest
      }
    });

    await expect(
      adapter.suggestDraft({
        currentDraft: {
          title: "Follow-up note",
          chiefComplaint: "Cough",
          illnessHistory: "Cough for two days.",
          clinicalSummary: "",
          medications: [],
          testsPrescribed: [],
          revisitDate: "",
          nextSteps: [],
          cautions: []
        },
        selectedChunks: []
      })
    ).rejects.toThrow("REPORT_CONTEXT_PROVIDER_HTTP_ERROR");
    expect(fallbackSuggest).not.toHaveBeenCalled();
  });
});
