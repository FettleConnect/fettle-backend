import {
  type DraftConsultationDocument,
  type DraftDocumentProvenance,
  type DraftFieldSource
} from "@medivoice/shared";

const DRAFT_SCALAR_FIELDS = [
  "title",
  "chiefComplaint",
  "illnessHistory",
  "clinicalSummary",
  "revisitDate",
  "nextSteps",
  "cautions"
] as const;

export function hasDraftContentChanged(
  previous: DraftConsultationDocument | undefined,
  next: DraftConsultationDocument
): boolean {
  if (!previous) {
    return true;
  }
  return JSON.stringify(previous) !== JSON.stringify(next);
}

export function mergeDraftWithManualLocks(
  _previousDraft: DraftConsultationDocument | undefined,
  _provenance: DraftDocumentProvenance | undefined,
  nextDraft: DraftConsultationDocument
): DraftConsultationDocument {
  return nextDraft;
}

export function mergeDraftProvenance(
  existing: DraftDocumentProvenance | undefined,
  previousDraft: DraftConsultationDocument | undefined,
  nextDraft: DraftConsultationDocument,
  source: DraftFieldSource
): DraftDocumentProvenance {
  const now = new Date().toISOString();
  const next: DraftDocumentProvenance = {
    medications: [],
    testsPrescribed: []
  };

  for (const field of DRAFT_SCALAR_FIELDS) {
    const previousValue = previousDraft?.[field];
    const nextValue = nextDraft[field];
    const fieldExisting = existing?.[field];
    const changed = JSON.stringify(previousValue ?? null) !== JSON.stringify(nextValue ?? null);
    next[field] = changed || !fieldExisting
      ? { source, locked: false, updatedAt: now }
      : fieldExisting;
  }

  next.medications = nextDraft.medications.map((item, index) => {
    const existingItem = existing?.medications[index];
    const previousItem = previousDraft?.medications[index];
    const changed = JSON.stringify(previousItem ?? null) !== JSON.stringify(item);
    return changed || !existingItem
      ? { source, locked: false, updatedAt: now }
      : existingItem;
  });

  next.testsPrescribed = nextDraft.testsPrescribed.map((item, index) => {
    const existingItem = existing?.testsPrescribed[index];
    const previousItem = previousDraft?.testsPrescribed[index];
    const changed = (previousItem ?? null) !== item;
    return changed || !existingItem
      ? { source, locked: false, updatedAt: now }
      : existingItem;
  });

  return next;
}
