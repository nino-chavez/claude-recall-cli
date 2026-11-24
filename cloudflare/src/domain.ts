import { z } from "zod";

export const MEMORY_STATUSES = [
  "Candidate",
  "Approved",
  "Contradicted",
  "Superseded",
  "Stale",
] as const;

export const MemoryStatusSchema = z.enum(MEMORY_STATUSES);

// One axis: what using the memory did to the run that retrieved it.
//   helped: following it moved the work forward as written.
//   misled: following it cost time or produced a wrong result.
export const MEMORY_OUTCOMES = ["helped", "misled"] as const;
export const MemoryOutcomeSchema = z.enum(MEMORY_OUTCOMES);

// One axis: how the source memory stands toward the target memory.
//   supersedes:     the source replaces the target (written only by set_memory_status).
//   requires:       applying the source needs the target applied too.
//   conflicts_with: the source and the target cannot both hold.
//   refines:        the source is a narrower, more specific case of the target.
export const LINK_RELATIONS = ["supersedes", "requires", "conflicts_with", "refines"] as const;
export const LinkRelationSchema = z.enum(LINK_RELATIONS);
export const ManualLinkRelationSchema = z.enum(["requires", "conflicts_with", "refines"]);

const CREDENTIAL_PATTERN =
  /(?:sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|cfk_[A-Za-z0-9_-]{24,}|sbp_[A-Za-z0-9]{20,}|sb(?:p|s)_[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|ey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})/;

export const CandidateEventSchema = z
  .object({
    event_id: z.string().regex(/^evt_[a-f0-9]{32}$/),
    schema_version: z.literal(1),
    event_type: z.literal("memory_candidate"),
    occurred_at: z.string().min(1).max(64),
    memory: z
      .object({
        stable_key: z.string().min(1).max(300),
        kind: z.string().min(1).max(80),
        title: z.string().min(1).max(120),
        body: z.string().min(1).max(6000),
        project: z.string().max(500).nullable().optional(),
        status: z.literal("Candidate"),
        confidence: z.number().min(0).max(1),
      })
      .strict(),
    provenance: z
      .object({
        source_table: z.literal("recipes"),
        source_row_id: z.string().min(1).max(100),
        source_client: z.enum(["claude", "codex", "gemini"]),
        source_machine: z.string().min(1).max(200),
        session_id: z.string().max(200).nullable().optional(),
        source_timestamp: z.string().max(64).nullable().optional(),
        curation_level: z.literal("manual_recipe"),
      })
      .strict(),
  })
  .strict()
  .superRefine((event, context) => {
    if (CREDENTIAL_PATTERN.test(JSON.stringify(event))) {
      context.addIssue({
        code: "custom",
        message: "credential-shaped values are not accepted",
      });
    }
  });

export function containsCredential(value: string): boolean {
  return CREDENTIAL_PATTERN.test(value);
}

export type CandidateEvent = z.infer<typeof CandidateEventSchema>;
export type MemoryStatus = z.infer<typeof MemoryStatusSchema>;

export interface DistilledMemory {
  title: string;
  body: string;
  kind: string;
  confidence: number;
}

export function deterministicDistillation(event: CandidateEvent): DistilledMemory {
  return {
    title: event.memory.title,
    body: event.memory.body,
    kind: event.memory.kind,
    confidence: event.memory.confidence,
  };
}

export function parseModelDistillation(
  value: unknown,
  fallback: DistilledMemory,
): DistilledMemory {
  if (typeof value !== "string") return fallback;
  const cleaned = value.trim().replace(/^```json\s*/i, "").replace(/\s*```$/, "");
  try {
    const parsed = z
      .object({
        title: z.string().min(1).max(120),
        body: z.string().min(1).max(6000),
        kind: z.string().min(1).max(80),
        confidence: z.number().min(0).max(1),
      })
      .strict()
      .parse(JSON.parse(cleaned));
    if (CREDENTIAL_PATTERN.test(JSON.stringify(parsed))) return fallback;
    return parsed;
  } catch {
    return fallback;
  }
}

export function ftsQuery(input: string): string {
  const terms = input
    .normalize("NFKC")
    .match(/[\p{L}\p{N}_-]+/gu)
    ?.slice(0, 12);
  return terms?.map((term) => `"${term.replaceAll('"', '""')}"`).join(" AND ") ?? "";
}
