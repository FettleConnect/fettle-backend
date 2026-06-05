import { randomUUID } from "node:crypto";

import { type TranscriptSegment } from "@medivoice/shared";

import {
  type TranscriptionAdapter,
  type TranscriptionAdapterInput,
  type TranscriptionAdapterResult
} from "./transcription-adapter-shared.js";

export class MockGeminiTranscriptionAdapter implements TranscriptionAdapter {
  readonly providerName = "mock-gemini";

  async transcribe(
    input: TranscriptionAdapterInput
  ): Promise<TranscriptionAdapterResult> {
    const duration = Math.max(30, Math.round(input.audio.durationSeconds || 90));
    const midpoint = Math.max(12, Math.round(duration / 2));
    const transcriptSegments: TranscriptSegment[] = [
      {
        id: randomUUID(),
        speaker: "doctor",
        speakerLabel: input.context.doctorName,
        text: `Good morning ${input.context.patientName}. Tell me what has been bothering you today.`,
        startedAtSeconds: 0,
        endedAtSeconds: Math.max(8, midpoint - 10)
      },
      {
        id: randomUUID(),
        speaker: "patient",
        speakerLabel: input.context.patientName,
        text: "I have had a persistent cough, throat irritation, and mild fever for the last three days.",
        startedAtSeconds: Math.max(8, midpoint - 9),
        endedAtSeconds: midpoint
      },
      {
        id: randomUUID(),
        speaker: "doctor",
        speakerLabel: input.context.doctorName,
        text: "This sounds like an upper respiratory infection. Stay hydrated, take the prescribed medicines, and come back if breathing worsens.",
        startedAtSeconds: midpoint,
        endedAtSeconds: duration
      }
    ];

    return {
      provider: this.providerName,
      requiresRecovery: false,
      summary: `${input.context.patientName} reported cough, throat irritation, and mild fever for three days. ${input.context.doctorName} advised supportive care and medication.`,
      transcriptSegments,
      metadata: {
        chiefComplaint: "Persistent cough and mild fever",
        symptoms: ["cough", "throat irritation", "mild fever"],
        diagnoses: ["Upper respiratory infection"],
        followUpPlan: "Review again if symptoms worsen or breathing difficulty develops.",
        careTeamNotes: ["Hydration encouraged", "Monitor fever trend"]
      },
      draftDocument: {
        title: "Consultation Draft",
        clinicalSummary:
          "Symptoms are consistent with an uncomplicated upper respiratory infection without current red-flag findings.",
        medications: [
          {
            medicationName: "Paracetamol",
            dosage: "500 mg",
            instructions: "Take one tablet every 6 to 8 hours if fever persists."
          },
          {
            medicationName: "Cough Syrup",
            dosage: "10 ml",
            instructions: "Take twice daily after food for five days."
          }
        ],
        testsPrescribed: ["CBC"],
        revisitDate: "Review in 5 days",
        nextSteps: [
          "Continue hydration and rest.",
          "Return for review if symptoms worsen."
        ],
        cautions: ["Seek urgent help if shortness of breath develops."]
      }
    };
  }
}
