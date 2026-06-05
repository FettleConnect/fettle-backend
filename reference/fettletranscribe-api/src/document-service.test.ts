import { describe, expect, it } from "vitest";

import { DocumentService } from "./document-service.js";

describe("DocumentService", () => {
  it("generates a PDF artifact and HTML from consultation data", () => {
    const service = new DocumentService();
    const generated = service.generatePdfArtifact({
      session: {
        id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        doctorId: "11111111-1111-4111-8111-111111111111",
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        },
        doctor: {
          name: "Dr. Rao"
        },
        status: "created",
        caseStatus: "open",
        createdAt: "2026-03-18T00:00:00.000Z",
        updatedAt: "2026-03-18T00:00:00.000Z"
      },
      draftDocument: {
        title: "Consultation Draft",
        chiefComplaint: "Fever and cough",
        illnessHistory:
          "Symptoms started three days ago and worsened at night. Fever is intermittent with cough.",
        clinicalSummary: "Likely uncomplicated respiratory infection.",
        medications: [
          {
            medicationName: "Paracetamol",
            dosage: "500 mg",
            instructions: "Twice daily"
          }
        ],
        testsPrescribed: ["CBC", "Chest X-ray"],
        revisitDate: "2099-05-06",
        nextSteps: ["Rest", "Hydration"],
        cautions: ["Return if breathlessness develops"]
      },
      metadata: {
        symptoms: ["cough", "fever"],
        diagnoses: ["Upper respiratory infection"],
        careTeamNotes: ["Hydration encouraged"]
      }
    });

    expect(generated.html).toContain("Consultation Draft");
    expect(generated.html).toContain("Fever and cough");
    expect(generated.html).toContain(
      "<h2>Illness History</h2><ul><li>Symptoms started three days ago and worsened at night</li>"
    );
    expect(generated.html).toContain("Medication Prescribed");
    expect(generated.html).toContain("Tests Prescribed");
    expect(generated.html.indexOf("Tests Prescribed")).toBeLessThan(
      generated.html.indexOf("Medication Prescribed")
    );
    expect(generated.html).not.toContain("Investigations");
    expect(generated.html).toContain("2099-05-06");
    expect(generated.html).toContain("data:image/jpeg;base64,");
    expect(generated.artifact.fileName).toContain("anita-sharma-");
    expect(generated.artifact.mimeType).toBe("application/pdf");
    expect(generated.artifact.delivery.whatsappMessage).toBe(
      "Hello Anita Sharma, your digital prescription is attached. Reply if you need help."
    );
    expect(generated.pdf.subarray(0, 8).toString()).toContain("%PDF-1.4");
    expect(generated.pdf.toString("latin1")).toContain("/Subtype /Image");
    expect(generated.pdf.toString("latin1")).toContain("/AmorLogo");
  });

  it("generates a paginated transcript pdf buffer", () => {
    const service = new DocumentService();
    const pdf = service.generateTranscriptPdf({
      session: {
        id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        doctorId: "11111111-1111-4111-8111-111111111111",
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        },
        doctor: {
          name: "Dr. Rao"
        },
        status: "created",
        caseStatus: "open",
        createdAt: "2026-03-18T00:00:00.000Z",
        updatedAt: "2026-03-18T00:00:00.000Z",
        transcription: {
          status: "completed",
          summary: "Patient reported fever, cough, and throat irritation for three days.",
          completedAt: "2026-03-18T00:10:00.000Z",
          transcriptSegments: Array.from({ length: 18 }, (_, index) => ({
            id: `segment-${index}`,
            speaker: index % 2 === 0 ? "doctor" : "patient",
            speakerLabel: index % 2 === 0 ? "Dr. Rao" : "Anita Sharma",
            text:
              index % 2 === 0
                ? "Please continue hydration, take the prescribed medicines, and monitor fever closely."
                : "I have been feeling tired with cough and low-grade fever, especially at night.",
            startedAtSeconds: index * 35,
            endedAtSeconds: index * 35 + 20
          }))
        }
      },
      transcription: {
        status: "completed",
        summary: "Patient reported fever, cough, and throat irritation for three days.",
        completedAt: "2026-03-18T00:10:00.000Z",
        transcriptSegments: Array.from({ length: 18 }, (_, index) => ({
          id: `segment-${index}`,
          speaker: index % 2 === 0 ? "doctor" : "patient",
          speakerLabel: index % 2 === 0 ? "Dr. Rao" : "Anita Sharma",
          text:
            index % 2 === 0
              ? "Please continue hydration, take the prescribed medicines, and monitor fever closely."
              : "I have been feeling tired with cough and low-grade fever, especially at night.",
          startedAtSeconds: index * 35,
          endedAtSeconds: index * 35 + 20
        }))
      }
    });

    expect(pdf.subarray(0, 8).toString()).toContain("%PDF-1.4");
    expect(pdf.toString("utf8")).toContain("Consultation Transcript");
    expect(pdf.toString("utf8")).toContain("CLINICAL SUMMARY");
    expect(pdf.toString("utf8")).toContain("Page 1");
  });

  it("paginates prescription pdf output for large medication payloads", () => {
    const service = new DocumentService();
    const generated = service.generatePdfArtifact({
      session: {
        id: "316f0eda-fbb5-478e-b15f-c9f18f5bfc14",
        doctorId: "11111111-1111-4111-8111-111111111111",
        patient: {
          name: "Anita Sharma",
          phoneNumber: "9876543210"
        },
        doctor: {
          name: "Dr. Rao"
        },
        status: "created",
        caseStatus: "open",
        createdAt: "2026-03-18T00:00:00.000Z",
        updatedAt: "2026-03-18T00:00:00.000Z"
      },
      draftDocument: {
        title:
          "Comprehensive Consultation Note for Persistent Respiratory Symptoms with Extended Follow-up Guidance",
        chiefComplaint:
          "Persistent cough with intermittent fever, throat irritation, and sleep disturbance for one week.",
        illnessHistory:
          "Symptoms started after travel and progressed despite home care. Patient reports fatigue and reduced appetite.",
        clinicalSummary:
          "Likely prolonged upper respiratory infection with inflammatory throat symptoms. Continue symptom relief and observe for red flags.",
        medications: Array.from({ length: 22 }, (_, index) => ({
          medicationName: `Medication-${index + 1} Extended Release Formula`,
          dosage: `${index + 1} tablet twice daily after food`,
          instructions:
            "Take with warm water after meals, avoid skipping doses, and continue hydration with electrolyte fluids."
        })),
        testsPrescribed: [
          "Complete blood count",
          "Chest X-ray PA view",
          "CRP quantitative assay"
        ],
        revisitDate: "2026-05-06",
        nextSteps: [
          "Return for review in 3 days or earlier if fever rises",
          "Monitor oxygen saturation at home twice daily"
        ],
        cautions: [
          "Visit emergency immediately if breathlessness or chest pain develops"
        ]
      }
    });

    const pdfText = generated.pdf.toString("latin1");
    const pageObjects = pdfText.match(/\/Type \/Page\b/g) ?? [];

    expect(pdfText).toContain("Comprehensive Consultation Note");
    expect(pageObjects.length).toBeGreaterThan(1);
  });

  it("omits past date input from prescription revisit metadata", () => {
    const service = new DocumentService();
    const normalized = service.normalizeDraftDocument({
      title: "Consultation Draft",
      chiefComplaint: "Fever",
      illnessHistory: "Fever since yesterday.",
      clinicalSummary: "Likely viral illness.",
      medications: [],
      testsPrescribed: [],
      revisitDate: "2026-05-05",
      nextSteps: [],
      cautions: []
    });

    expect(normalized.revisitDate).toBeUndefined();
  });
});
