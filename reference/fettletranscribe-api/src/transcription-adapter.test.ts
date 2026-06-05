import { describe, expect, it } from "vitest";

import {
  FallbackTranscriptionAdapter,
  GeminiTranscriptionAdapter,
  OpenAITranscriptionAdapter
} from "./transcription-adapter.js";

describe("GeminiTranscriptionAdapter", () => {
  const mockOpenAINormalizedAudio = async (audio: {
    fileName: string;
    mimeType: string;
    durationSeconds: number;
    data: Buffer;
  }) => ({
    ...audio,
    fileName: "consultation-openai.mp3",
    mimeType: "audio/mpeg",
    data: Buffer.from("normalized-audio")
  });

  it("normalizes nullable metadata and draft document fields", async () => {
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      fetchImpl: async () =>
        ({
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Consultation captured successfully.",
                        transcriptSegments: [
                          {
                            speaker: "doctor",
                            speakerLabel: "Dr. Rao",
                            text: "Please describe your symptoms.",
                            startedAtSeconds: 0,
                            endedAtSeconds: 8
                          }
                        ],
                        metadata: {
                          chiefComplaint: null,
                          symptoms: null,
                          diagnoses: ["Upper respiratory infection"],
                          followUpPlan: null,
                          careTeamNotes: null
                        },
                        draftDocument: {
                          title: null,
                          chiefComplaint: null,
                          illnessHistory: null,
                          clinicalSummary: null,
                          medications: null,
                          nextSteps: null,
                          cautions: null
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        }) as Response
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(result.metadata.chiefComplaint).toBeUndefined();
    expect(result.metadata.symptoms).toEqual([]);
    expect(result.metadata.diagnoses).toEqual(["Upper respiratory infection"]);
    expect(result.draftDocument.title).toBe("Consultation Draft");
    expect(result.draftDocument.illnessHistory).toBe("Upper respiratory infection");
    expect(result.draftDocument.medications).toEqual([]);
    expect(result.draftDocument.nextSteps).toEqual([]);
    expect(result.draftDocument.cautions).toEqual([]);
    expect(result.draftDocument.clinicalSummary).toContain("Anita Sharma");
  });

  it("does not require illness history when diagnoses are empty", async () => {
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      fetchImpl: async () =>
        ({
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Consultation captured successfully.",
                        transcriptSegments: [
                          {
                            speaker: "doctor",
                            speakerLabel: "Dr. Rao",
                            text: "Please describe your symptoms.",
                            startedAtSeconds: 0,
                            endedAtSeconds: 8
                          }
                        ],
                        metadata: {
                          chiefComplaint: "",
                          symptoms: [],
                          diagnoses: [],
                          followUpPlan: "",
                          careTeamNotes: []
                        },
                        draftDocument: {
                          title: "Consultation Draft",
                          chiefComplaint: "",
                          illnessHistory: "",
                          clinicalSummary: "Symptoms discussed during the consultation.",
                          medications: [],
                          testsPrescribed: [],
                          nextSteps: [],
                          cautions: []
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        }) as Response
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(result.draftDocument.illnessHistory).toBeUndefined();
    expect(result.draftDocument.chiefComplaint).toBeUndefined();
  });

  it("infers tests from next steps, removes duplicates, and prefers scheduled follow-up text", async () => {
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      fetchImpl: async () =>
        ({
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Consultation captured successfully.",
                        transcriptSegments: [
                          {
                            speaker: "doctor",
                            speakerLabel: "Dr. Sharma",
                            text: "Mee neck stiffness ela undi?",
                            startedAtSeconds: 0,
                            endedAtSeconds: 8
                          }
                        ],
                        metadata: {
                          chiefComplaint: "Headaches",
                          symptoms: ["Headache", "Neck stiffness"],
                          diagnoses: ["Tension headache"],
                          followUpPlan:
                            "Follow-up appointment scheduled for Tuesday, April 7th, 2026, to review progress and X-ray results.",
                          careTeamNotes: []
                        },
                        draftDocument: {
                          title: "Medical Consultation Summary - Shreyash",
                          chiefComplaint: "Headaches",
                          illnessHistory: "Tension headache",
                          clinicalSummary:
                            "Patient reports recurrent headaches and neck stiffness.",
                          medications: [
                            {
                              medicationName: "Ibuprofen",
                              dosage: "500 mg",
                              instructions:
                                "Take one tablet twice daily for 5 days."
                            }
                          ],
                          testsPrescribed: [],
                          revisitDate: "",
                          nextSteps: [
                            "Obtain a cervical spine X-ray next week.",
                            "Maintain awareness of posture.",
                            "Follow-up appointment scheduled for Tuesday, April 7th, 2026, to review progress."
                          ],
                          cautions: []
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        }) as Response
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Shreyash",
        doctorName: "Dr. Sharma"
      }
    });

    expect(result.draftDocument.testsPrescribed).toEqual([
      "Obtain a cervical spine X-ray next week."
    ]);
    expect(result.draftDocument.revisitDate).toBe("2026-04-07");
    expect(result.draftDocument.nextSteps).toEqual([
      "Maintain awareness of posture.",
      "Follow-up appointment scheduled for Tuesday, April 7th, 2026, to review progress."
    ]);
  });

  it("merges explicit tests with additional investigations buried in next steps", async () => {
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      fetchImpl: async () =>
        ({
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Consultation captured successfully.",
                        transcriptSegments: [
                          {
                            speaker: "doctor",
                            speakerLabel: "Dr. Sharma",
                            text: "Please get the blood work and chest imaging done.",
                            startedAtSeconds: 0,
                            endedAtSeconds: 8
                          }
                        ],
                        metadata: {
                          chiefComplaint: "Persistent cough",
                          symptoms: ["Persistent cough"],
                          diagnoses: ["Bronchitis"],
                          followUpPlan:
                            "Return for review if symptoms worsen.",
                          careTeamNotes: []
                        },
                        draftDocument: {
                          title: "Medical Consultation Summary - Shreyash",
                          chiefComplaint: "Persistent cough",
                          illnessHistory: "Bronchitis",
                          clinicalSummary:
                            "Patient reports persistent cough without acute distress.",
                          medications: [],
                          testsPrescribed: ["CBC"],
                          revisitDate: "",
                          nextSteps: [
                            "Schedule a chest X-ray this week.",
                            "Use steam inhalation twice daily."
                          ],
                          cautions: []
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        }) as Response
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Shreyash",
        doctorName: "Dr. Sharma"
      }
    });

    expect(result.draftDocument.testsPrescribed).toEqual([
      "CBC",
      "Schedule a chest X-ray this week."
    ]);
    expect(result.draftDocument.nextSteps).toEqual([
      "Use steam inhalation twice daily."
    ]);
    expect(result.draftDocument.revisitDate).toBeUndefined();
  });

  it("does not treat auxiliary may or generic review instructions as dated follow-up", async () => {
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      fetchImpl: async () =>
        ({
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Consultation captured successfully.",
                        transcriptSegments: [
                          {
                            speaker: "doctor",
                            speakerLabel: "Dr. Sharma",
                            text: "Come back next Monday if the cough continues.",
                            startedAtSeconds: 0,
                            endedAtSeconds: 8
                          }
                        ],
                        metadata: {
                          chiefComplaint: "Persistent cough",
                          symptoms: ["Persistent cough"],
                          diagnoses: ["Viral upper respiratory infection"],
                          followUpPlan:
                            "Please review the X-ray images with the specialist.",
                          careTeamNotes: []
                        },
                        draftDocument: {
                          title: "Medical Consultation Summary - Shreyash",
                          chiefComplaint: "Persistent cough",
                          illnessHistory: "Viral upper respiratory infection",
                          clinicalSummary:
                            "Patient reports persistent cough without shortness of breath.",
                          medications: [],
                          testsPrescribed: [],
                          revisitDate: "",
                          nextSteps: [
                            "Return for review as symptoms may persist.",
                            "Come back next Monday for reassessment."
                          ],
                          cautions: []
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        }) as Response
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Shreyash",
        doctorName: "Dr. Sharma"
      }
    });

    expect(result.draftDocument.revisitDate).toBe(nextWeekdayIso(1));
  });

  it("chunks oversized audio and merges transcript timelines before synthesis", async () => {
    let requestCount = 0;
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      splitAudioImpl: async () => [
        {
          fileName: "chunk-001.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-1")
        },
        {
          fileName: "chunk-002.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-2")
        }
      ],
      fetchImpl: async () => {
        requestCount += 1;
        if (requestCount === 1 || requestCount === 2) {
          const segmentText = requestCount === 1
            ? "First chunk dialogue"
            : "Second chunk dialogue";
          return {
            ok: true,
            json: async () => ({
              candidates: [
                {
                  content: {
                    parts: [
                      {
                        text: JSON.stringify({
                          summary: `Chunk ${requestCount} summary`,
                          transcriptSegments: [
                            {
                              speaker: "doctor",
                              speakerLabel: "Dr. Rao",
                              text: segmentText,
                              startedAtSeconds: 0,
                              endedAtSeconds: 10
                            }
                          ],
                          metadata: {
                            symptoms: [],
                            diagnoses: [],
                            careTeamNotes: []
                          },
                          draftDocument: {
                            title: "Consultation Draft",
                            clinicalSummary: "Interim summary.",
                            medications: [],
                            testsPrescribed: [],
                            nextSteps: [],
                            cautions: []
                          }
                        })
                      }
                    ]
                  }
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Merged summary",
                        metadata: {
                          symptoms: ["cough"],
                          diagnoses: ["URI"],
                          careTeamNotes: []
                        },
                        draftDocument: {
                          title: "Consultation Draft",
                          clinicalSummary: "Merged clinical summary.",
                          medications: [],
                          testsPrescribed: [],
                          nextSteps: ["Hydrate"],
                          cautions: []
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 1200,
        data: Buffer.alloc(16_000_000, 1)
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(requestCount).toBe(3);
    expect(result.summary).toBe("Merged summary");
    expect(result.transcriptSegments).toHaveLength(2);
    expect(result.transcriptSegments[0]?.startedAtSeconds).toBe(0);
    expect(result.transcriptSegments[1]?.startedAtSeconds).toBeGreaterThanOrEqual(600);
  });

  it("fails chunked transcription when any chunk fails after retries", async () => {
    let requestCount = 0;
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      splitAudioImpl: async () => [
        {
          fileName: "chunk-001.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-1")
        },
        {
          fileName: "chunk-002.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-2")
        }
      ],
      fetchImpl: async () => {
        requestCount += 1;
        if (requestCount === 1) {
          return {
            ok: true,
            json: async () => ({
              candidates: [
                {
                  content: {
                    parts: [
                      {
                        text: JSON.stringify({
                          summary: "Chunk 1 summary",
                          transcriptSegments: [
                            {
                              speaker: "doctor",
                              speakerLabel: "Dr. Rao",
                              text: "First chunk dialogue",
                              startedAtSeconds: 0,
                              endedAtSeconds: 10
                            }
                          ],
                          metadata: {
                            symptoms: [],
                            diagnoses: [],
                            careTeamNotes: []
                          },
                          draftDocument: {
                            title: "Consultation Draft",
                            clinicalSummary: "Interim summary.",
                            medications: [],
                            testsPrescribed: [],
                            nextSteps: [],
                            cautions: []
                          }
                        })
                      }
                    ]
                  }
                }
              ]
            })
          } as Response;
        }
        if (requestCount >= 2) {
          return {
            ok: false,
            status: 503
          } as Response;
        }
        throw new Error("unexpected request");
      }
    });

    await expect(
      adapter.transcribe({
        sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        audio: {
          fileName: "consultation.webm",
          mimeType: "audio/webm",
          durationSeconds: 1200,
          data: Buffer.alloc(16_000_000, 1)
        },
        context: {
          patientName: "Anita Sharma",
          doctorName: "Dr. Rao"
        }
      })
    ).rejects.toThrow(
      "Unable to complete transcription for all audio parts. Please retry transcription."
    );

    expect(requestCount).toBe(10);
  });

  it("reconciles duplicate boundary segments and smooths unknown speaker labels", async () => {
    let requestCount = 0;
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      splitAudioImpl: async () => [
        {
          fileName: "chunk-001.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-1")
        },
        {
          fileName: "chunk-002.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-2")
        }
      ],
      fetchImpl: async () => {
        requestCount += 1;
        if (requestCount === 1) {
          return {
            ok: true,
            json: async () => ({
              candidates: [
                {
                  content: {
                    parts: [
                      {
                        text: JSON.stringify({
                          summary: "Chunk 1 summary",
                          transcriptSegments: [
                            {
                              speaker: "doctor",
                              speakerLabel: "Dr. Rao",
                              text: "Take paracetamol 500 mg twice daily.",
                              startedAtSeconds: 585,
                              endedAtSeconds: 600
                            }
                          ],
                          metadata: { symptoms: [], diagnoses: [], careTeamNotes: [] },
                          draftDocument: {
                            title: "Consultation Draft",
                            clinicalSummary: "Interim summary.",
                            medications: [],
                            testsPrescribed: [],
                            nextSteps: [],
                            cautions: []
                          }
                        })
                      }
                    ]
                  }
                }
              ]
            })
          } as Response;
        }
        if (requestCount === 2) {
          return {
            ok: true,
            json: async () => ({
              candidates: [
                {
                  content: {
                    parts: [
                      {
                        text: JSON.stringify({
                          summary: "Chunk 2 summary",
                          transcriptSegments: [
                            {
                              speaker: "unknown",
                              speakerLabel: "Speaker",
                              text: "Take paracetamol 500 mg twice daily",
                              startedAtSeconds: 0,
                              endedAtSeconds: 12
                            },
                            {
                              speaker: "doctor",
                              speakerLabel: "Dr. Rao",
                              text: "Continue hydration and review in five days.",
                              startedAtSeconds: 12,
                              endedAtSeconds: 28
                            }
                          ],
                          metadata: { symptoms: [], diagnoses: [], careTeamNotes: [] },
                          draftDocument: {
                            title: "Consultation Draft",
                            clinicalSummary: "Interim summary.",
                            medications: [],
                            testsPrescribed: [],
                            nextSteps: [],
                            cautions: []
                          }
                        })
                      }
                    ]
                  }
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Merged summary",
                        metadata: { symptoms: [], diagnoses: [], careTeamNotes: [] },
                        draftDocument: {
                          title: "Consultation Draft",
                          clinicalSummary: "Merged summary.",
                          medications: [],
                          testsPrescribed: [],
                          nextSteps: [],
                          cautions: []
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 1200,
        data: Buffer.alloc(16_000_000, 1)
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(result.transcriptSegments).toHaveLength(2);
    expect(
      result.transcriptSegments.filter((segment) =>
        segment.text.toLowerCase().includes("paracetamol 500 mg")
      )
    ).toHaveLength(1);
    expect(result.transcriptSegments[0]?.speaker).toBe("doctor");
    expect(result.transcriptSegments[1]?.speaker).toBe("doctor");
  });

  it("fails chunked transcription when all chunks fail after retries", async () => {
    let requestCount = 0;
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      splitAudioImpl: async () => [
        {
          fileName: "chunk-001.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-1")
        },
        {
          fileName: "chunk-002.webm",
          mimeType: "audio/webm",
          durationSeconds: 600,
          data: Buffer.from("chunk-2")
        }
      ],
      fetchImpl: async () => {
        requestCount += 1;
        return {
          ok: false,
          status: 503
        } as Response;
      }
    });

    await expect(
      adapter.transcribe({
        sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        audio: {
          fileName: "consultation.webm",
          mimeType: "audio/webm",
          durationSeconds: 1200,
          data: Buffer.alloc(16_000_000, 1)
        },
        context: {
          patientName: "Anita Sharma",
          doctorName: "Dr. Rao"
        }
      })
    ).rejects.toThrow(
      "Unable to transcribe consultation audio after retrying all audio parts."
    );
    expect(requestCount).toBe(18);
  }, 15000);

  it("uses OpenAI diarized transcription and structured synthesis", async () => {
    const requestedUrls: string[] = [];
    const uploadedFiles: File[] = [];
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input, init) => {
        requestedUrls.push(String(input));

        if (String(input).endsWith("/audio/transcriptions")) {
          const file = init?.body instanceof FormData ? init.body.get("file") : null;
          if (file instanceof File) {
            uploadedFiles.push(file);
          }
          return {
            ok: true,
            json: async () => ({
              text: "Please take paracetamol and come back Monday.",
              segments: [
                {
                  speaker: "doctor",
                  text: "Please take paracetamol and come back Monday.",
                  start: 0,
                  end: 8
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Medication and follow-up were discussed.",
              metadata: {
                chiefComplaint: "Fever",
                symptoms: ["Fever"],
                diagnoses: [],
                followUpPlan: "Come back Monday.",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "Fever",
                illnessHistory: "",
                clinicalSummary:
                  "Patient was advised medication and follow-up.",
                medications: [
                  {
                    medicationName: "Paracetamol",
                    dosage: "500 mg",
                    instructions: "Take as advised."
                  }
                ],
                testsPrescribed: [],
                revisitDate: "Come back Monday.",
                nextSteps: ["Come back Monday."],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(requestedUrls).toEqual([
      "https://api.openai.com/v1/audio/transcriptions",
      "https://api.openai.com/v1/responses"
    ]);
    expect(uploadedFiles[0]?.name).toBe("consultation.webm");
    expect(uploadedFiles[0]?.type).toBe("audio/webm");
    expect(result.provider).toBe(
      "openai:gpt-4o-transcribe-diarize+gpt-5.4-mini"
    );
    expect(result.transcriptSegments[0]?.speaker).toBe("doctor");
    expect(result.draftDocument.medications[0]?.medicationName).toBe(
      "Paracetamol"
    );
  });

  it("pre-normalizes risky OpenAI audio formats before first transcription request", async () => {
    const uploadedFiles: File[] = [];
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input, init) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          const file = init?.body instanceof FormData ? init.body.get("file") : null;
          if (file instanceof File) {
            uploadedFiles.push(file);
          }
          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "doctor",
                  text: "Normalized upfront.",
                  start: 0,
                  end: 3
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Normalized upfront.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary: "Normalized upfront.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.ogg",
        mimeType: "audio/ogg",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(uploadedFiles[0]?.name).toBe("consultation-openai.mp3");
    expect(uploadedFiles[0]?.type).toBe("audio/mpeg");
  });

  it("falls back to original audio when OpenAI audio normalization fails", async () => {
    const uploadedFiles: File[] = [];
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: async (audio) => audio,
      fetchImpl: async (input, init) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          const file = init?.body instanceof FormData ? init.body.get("file") : null;
          if (file instanceof File) {
            uploadedFiles.push(file);
          }
          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "patient",
                  text: "Original audio accepted.",
                  start: 0,
                  end: 3
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Original audio accepted.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary: "Original audio accepted.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(uploadedFiles[0]?.name).toBe("consultation.webm");
    expect(uploadedFiles[0]?.type).toBe("audio/webm");
  });

  it("normalizes audio only after OpenAI rejects original audio format", async () => {
    const uploadedFiles: File[] = [];
    let transcriptionCalls = 0;
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input, init) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          transcriptionCalls += 1;
          const file = init?.body instanceof FormData ? init.body.get("file") : null;
          if (file instanceof File) {
            uploadedFiles.push(file);
          }
          if (transcriptionCalls === 1) {
            return new Response(
              JSON.stringify({
                error: {
                  message: "Audio file might be corrupted or unsupported",
                  type: "invalid_request_error",
                  param: "file",
                  code: "invalid_value"
                }
              }),
              { status: 400, statusText: "Bad Request" }
            );
          }
          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "patient",
                  text: "Recovered after normalization.",
                  start: 0,
                  end: 3
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Recovered after normalization.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary: "Recovered after normalization.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(transcriptionCalls).toBe(2);
    expect(uploadedFiles[0]?.name).toBe("consultation.webm");
    expect(uploadedFiles[0]?.type).toBe("audio/webm");
    expect(uploadedFiles[1]?.name).toBe("consultation-openai.mp3");
    expect(uploadedFiles[1]?.type).toBe("audio/mpeg");
    expect(result.summary).toBe("Recovered after normalization.");
  });

  it("retries OpenAI transcription up to two total attempts for transient failures", async () => {
    let transcriptionAttempts = 0;
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          transcriptionAttempts += 1;
          if (transcriptionAttempts < 2) {
            return new Response(
              JSON.stringify({ error: "Service unavailable" }),
              { status: 503, statusText: "Service Unavailable" }
            );
          }

          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "doctor",
                  text: "Second attempt worked.",
                  start: 0,
                  end: 4
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Second attempt worked.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary: "Second attempt worked.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(transcriptionAttempts).toBe(2);
    expect(result.summary).toBe("Second attempt worked.");
  });

  it("retries OpenAI synthesis when provider returns malformed JSON", async () => {
    let synthesisAttempts = 0;
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "doctor",
                  text: "Synthesis retry needed.",
                  start: 0,
                  end: 4
                }
              ]
            })
          } as Response;
        }

        synthesisAttempts += 1;
        if (synthesisAttempts === 1) {
          return {
            ok: true,
            json: async () => ({
              output_text: "{\"summary\":\"broken\""
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Recovered synthesis output.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary: "Recovered synthesis output.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(synthesisAttempts).toBe(2);
    expect(result.summary).toBe("Recovered synthesis output.");
  });

  it("does not retry OpenAI transcription for non-retryable 400 errors", async () => {
    let transcriptionAttempts = 0;
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: async (audio) => audio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          transcriptionAttempts += 1;
          return new Response(
            JSON.stringify({
              error: {
                message: "Bad request",
                type: "invalid_request_error"
              }
            }),
            { status: 400, statusText: "Bad Request" }
          );
        }

        return {
          ok: true,
          json: async () => ({ output_text: "{}" })
        } as Response;
      }
    });

    await expect(
      adapter.transcribe({
        sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        audio: {
          fileName: "consultation.webm",
          mimeType: "audio/webm",
          durationSeconds: 42,
          data: Buffer.from("audio")
        },
        context: {
          patientName: "Anita Sharma",
          doctorName: "Dr. Rao"
        }
      })
    ).rejects.toThrow("OpenAI transcription failed with status 400.");

    expect(transcriptionAttempts).toBe(1);
  });

  it("retries Gemini transcription on transient provider status", async () => {
    let attempts = 0;
    const adapter = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      fetchImpl: async () => {
        attempts += 1;
        if (attempts < 3) {
          return new Response("Service unavailable", { status: 503 });
        }
        return {
          ok: true,
          json: async () => ({
            candidates: [
              {
                content: {
                  parts: [
                    {
                      text: JSON.stringify({
                        summary: "Recovered Gemini output.",
                        transcriptSegments: [
                          {
                            speaker: "doctor",
                            speakerLabel: "Dr. Rao",
                            text: "Recovered Gemini output.",
                            startedAtSeconds: 0,
                            endedAtSeconds: 4
                          }
                        ],
                        metadata: {
                          symptoms: [],
                          diagnoses: [],
                          careTeamNotes: []
                        },
                        draftDocument: {
                          title: "Consultation Draft",
                          clinicalSummary: "Recovered Gemini output.",
                          medications: [],
                          testsPrescribed: [],
                          nextSteps: [],
                          cautions: []
                        }
                      })
                    }
                  ]
                }
              }
            ]
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(attempts).toBe(3);
    expect(result.summary).toBe("Recovered Gemini output.");
  });

  it("falls back to OpenAI when Gemini fails", async () => {
    const primary = new GeminiTranscriptionAdapter({
      apiKey: "test-api-key",
      fetchImpl: async () =>
        ({
          ok: false,
          status: 503
        }) as Response
    });
    const fallback = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          return {
            ok: true,
            json: async () => ({
              text: "Recovered transcript.",
              segments: [
                {
                  speaker: "patient",
                  text: "Recovered transcript.",
                  start: 0,
                  end: 5
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Recovered with OpenAI.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary: "Recovered transcript.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });
    const adapter = new FallbackTranscriptionAdapter({
      primary,
      fallback
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(result.provider).toBe(
      "openai:gpt-4o-transcribe-diarize+gpt-5.4-mini"
    );
    expect(result.summary).toBe("Recovered with OpenAI.");
  });

  it("marks garbled OpenAI transcripts for recovery and avoids broad doctor role guessing", async () => {
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          return {
            ok: true,
            json: async () => ({
              text: "Start recording and printer discussion.",
              segments: [
                {
                  speaker: "A",
                  text: "Did you start recording end of it?",
                  start: 0,
                  end: 3
                },
                {
                  speaker: "B",
                  text: "तो तुमारे ही जाया कर रहे थे",
                  start: 4,
                  end: 7
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary:
                "The transcript is too garbled and does not contain a clinical consultation.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: [
                  "Transcript is not a usable medical consultation note."
                ]
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary:
                  "No clinical information could be reliably extracted.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 42,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(result.requiresRecovery).toBe(true);
    expect(result.transcriptSegments[0]?.speaker).toBe("unknown");
  });

  it("marks single-speaker synthetic dialogue blobs for recovery", async () => {
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "doctor",
                  text:
                    "Good morning doctor. Good morning Athulia. How are you? I'm fine doctor. What brings you here? Doctor I have been having pain near my toes and it hurts a lot. It is swelling there also. What happened? I don't know doctor. I will prescribe medicines. Just apply and come back.",
                  start: 0,
                  end: 40
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Patient reports toe pain and swelling.",
              metadata: {
                symptoms: ["toe pain", "swelling"],
                diagnoses: [],
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                clinicalSummary: "Toe pain with swelling.",
                medications: [],
                testsPrescribed: [],
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "f31a2063-19f7-4bb5-9ac6-8395fa85b4b2",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 41,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Atulya",
        doctorName: "Dr. Anitha Rayirala"
      }
    });

    expect(result.requiresRecovery).toBe(true);
  });

  it("reclassifies contradictory patient-labelled clinician turns to doctor", async () => {
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "doctor",
                  text: "Please continue medicine and come back in three days.",
                  start: 0,
                  end: 4
                },
                {
                  speaker: "patient",
                  text: "I am prescribing azithromycin 500 mg. Please review in three days.",
                  start: 4,
                  end: 8
                },
                {
                  speaker: "doctor",
                  text: "Monitor fever and update me if symptoms worsen.",
                  start: 8,
                  end: 12
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Follow-up instructions were discussed.",
              metadata: {
                chiefComplaint: "",
                symptoms: [],
                diagnoses: [],
                followUpPlan: "",
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                chiefComplaint: "",
                illnessHistory: "",
                clinicalSummary: "Follow-up instructions were discussed.",
                medications: [],
                testsPrescribed: [],
                revisitDate: "",
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 30,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Anita Sharma",
        doctorName: "Dr. Rao"
      }
    });

    expect(result.transcriptSegments[1]?.speaker).toBe("doctor");
    expect(result.transcriptSegments[1]?.speakerLabel).toBe("Dr. Rao");
  });

  it("splits all-patient labeled two-voice turns using utterance evidence", async () => {
    const adapter = new OpenAITranscriptionAdapter({
      apiKey: "test-openai-key",
      normalizeAudioImpl: mockOpenAINormalizedAudio,
      fetchImpl: async (input) => {
        if (String(input).endsWith("/audio/transcriptions")) {
          return {
            ok: true,
            json: async () => ({
              segments: [
                {
                  speaker: "patient",
                  speaker_label: "Atulya S Rai",
                  text: "I am feeling a swelling on my head like a small egg.",
                  start: 0,
                  end: 3
                },
                {
                  speaker: "patient",
                  speaker_label: "Atulya S Rai",
                  text: "I am sorry to hear that. Would you describe the ache a bit more?",
                  start: 3,
                  end: 7
                },
                {
                  speaker: "patient",
                  speaker_label: "Atulya S Rai",
                  text: "I am having problems while walking.",
                  start: 7,
                  end: 10
                }
              ]
            })
          } as Response;
        }

        return {
          ok: true,
          json: async () => ({
            output_text: JSON.stringify({
              summary: "Head swelling and gait discomfort discussed.",
              metadata: {
                symptoms: ["head swelling", "difficulty walking"],
                diagnoses: [],
                careTeamNotes: []
              },
              draftDocument: {
                title: "Consultation Draft",
                clinicalSummary: "Head swelling with walking difficulty.",
                medications: [],
                testsPrescribed: [],
                nextSteps: [],
                cautions: []
              }
            })
          })
        } as Response;
      }
    });

    const result = await adapter.transcribe({
      sessionId: "0bc9e91c-30ec-4ef7-8877-0fb8ed2f6b4d",
      audio: {
        fileName: "consultation.webm",
        mimeType: "audio/webm",
        durationSeconds: 20,
        data: Buffer.from("audio")
      },
      context: {
        patientName: "Atulya S Rai",
        doctorName: "Dr. Anitha Rayirala"
      }
    });

    expect(result.transcriptSegments[0]?.speaker).toBe("patient");
    expect(result.transcriptSegments[1]?.speaker).toBe("doctor");
    expect(result.transcriptSegments[2]?.speaker).toBe("patient");
  });
});

function nextWeekdayIso(targetWeekday: number, from = new Date()): string {
  const start = new Date(from);
  start.setHours(0, 0, 0, 0);
  const delta = ((targetWeekday - start.getDay() + 7) % 7) || 7;
  start.setDate(start.getDate() + delta);
  const year = start.getFullYear();
  const month = String(start.getMonth() + 1).padStart(2, "0");
  const day = String(start.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
