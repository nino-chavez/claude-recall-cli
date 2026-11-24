// Outcome reports and typed links between memories. Neither changes a memory's
// status: outcomes only weight search order, links only add context.

import { containsCredential } from "./domain";

type Db = D1Database;

export interface MemoryLinkView {
  relation: string;
  direction: "outgoing" | "incoming";
  memory_id: string;
  title: string;
  status: string;
  reason: string;
  actor: string;
}

export type GraphResult<T> = { ok: true; value: T } | { ok: false; error: string };

export function toolReply<T>(result: GraphResult<T>) {
  return result.ok
    ? {
        content: [{ type: "text" as const, text: JSON.stringify(result.value) }],
        structuredContent: result.value as Record<string, unknown>,
      }
    : { content: [{ type: "text" as const, text: result.error }], isError: true };
}

const fail = (error: string): { ok: false; error: string } => ({ ok: false, error });

async function memoryExists(db: Db, id: string): Promise<boolean> {
  const row = await db.prepare("SELECT 1 AS found FROM memories WHERE id = ?").bind(id).first();
  return row !== null;
}

export async function reportOutcome(
  db: Db,
  input: { id: string; outcome: string; session_id: string; actor: string; note?: string },
): Promise<GraphResult<{ recorded: boolean; helped: number; misled: number }>> {
  const note = input.note?.trim() || null;
  if (note && containsCredential(note)) return fail("credential-shaped values are not accepted");
  if (!(await memoryExists(db, input.id))) return fail("Memory not found");
  const outcomeId = `out_${crypto.randomUUID().replaceAll("-", "")}`;
  const inserted = await db
    .prepare(
      `INSERT OR IGNORE INTO memory_outcomes
         (outcome_id, memory_id, outcome, session_id, actor, note)
       VALUES (?, ?, ?, ?, ?, ?)`,
    )
    .bind(outcomeId, input.id, input.outcome, input.session_id, input.actor, note)
    .run();
  const tally = await outcomeTally(db, input.id);
  return { ok: true, value: { recorded: (inserted.meta.changes ?? 0) > 0, ...tally } };
}

export async function outcomeTally(db: Db, id: string): Promise<{ helped: number; misled: number }> {
  const row = await db
    .prepare(
      `SELECT COALESCE(SUM(outcome = 'helped'), 0) AS helped,
              COALESCE(SUM(outcome = 'misled'), 0) AS misled
       FROM memory_outcomes WHERE memory_id = ?`,
    )
    .bind(id)
    .first<{ helped: number; misled: number }>();
  return { helped: row?.helped ?? 0, misled: row?.misled ?? 0 };
}

async function checkPair(db: Db, from: string, to: string): Promise<string | null> {
  if (from === to) return "A memory cannot link to itself";
  if (!(await memoryExists(db, from))) return `Memory not found: ${from}`;
  if (!(await memoryExists(db, to))) return `Memory not found: ${to}`;
  return null;
}

export async function linkMemories(
  db: Db,
  input: { from_id: string; to_id: string; relation: string; reason: string; actor: string },
): Promise<GraphResult<{ created: boolean; event_id: string | null }>> {
  if (containsCredential(input.reason)) return fail("credential-shaped values are not accepted");
  const problem = await checkPair(db, input.from_id, input.to_id);
  if (problem) return fail(problem);
  const inserted = await db
    .prepare(
      `INSERT OR IGNORE INTO memory_links
         (from_memory_id, to_memory_id, relation, reason, actor)
       VALUES (?, ?, ?, ?, ?)`,
    )
    .bind(input.from_id, input.to_id, input.relation, input.reason, input.actor)
    .run();
  if ((inserted.meta.changes ?? 0) === 0) return { ok: true, value: { created: false, event_id: null } };
  const eventId = `link_${crypto.randomUUID()}`;
  await linkEvent(db, eventId, "link_added", input);
  return { ok: true, value: { created: true, event_id: eventId } };
}

export async function unlinkMemories(
  db: Db,
  input: { from_id: string; to_id: string; relation: string; reason: string; actor: string },
): Promise<GraphResult<{ removed: boolean; event_id: string | null }>> {
  if (input.relation === "supersedes") {
    return fail("supersedes links follow the Superseded status; change the status with set_memory_status");
  }
  const deleted = await db
    .prepare(
      `DELETE FROM memory_links
       WHERE from_memory_id = ? AND to_memory_id = ? AND relation = ?`,
    )
    .bind(input.from_id, input.to_id, input.relation)
    .run();
  if ((deleted.meta.changes ?? 0) === 0) return { ok: true, value: { removed: false, event_id: null } };
  const eventId = `unlink_${crypto.randomUUID()}`;
  await linkEvent(db, eventId, "link_removed", input);
  return { ok: true, value: { removed: true, event_id: eventId } };
}

async function linkEvent(
  db: Db,
  eventId: string,
  type: "link_added" | "link_removed",
  input: { from_id: string; to_id: string; relation: string; reason: string; actor: string },
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO memory_events (event_id, memory_id, event_type, reason, actor, payload_json)
       VALUES (?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      eventId,
      input.from_id,
      type,
      input.reason,
      input.actor,
      JSON.stringify({ relation: input.relation, to_memory_id: input.to_id }),
    )
    .run();
}

export async function linksFor(db: Db, ids: string[]): Promise<Map<string, MemoryLinkView[]>> {
  const byId = new Map<string, MemoryLinkView[]>(ids.map((id) => [id, []]));
  if (ids.length === 0) return byId;
  const list = JSON.stringify(ids);
  const rows = await db
    .prepare(
      `SELECT l.from_memory_id AS self, 'outgoing' AS direction, l.relation, l.reason, l.actor,
              n.id AS memory_id, n.title, n.status
       FROM memory_links l JOIN memories n ON n.id = l.to_memory_id
       WHERE l.from_memory_id IN (SELECT value FROM json_each(?))
       UNION ALL
       SELECT l.to_memory_id AS self, 'incoming' AS direction, l.relation, l.reason, l.actor,
              n.id AS memory_id, n.title, n.status
       FROM memory_links l JOIN memories n ON n.id = l.from_memory_id
       WHERE l.to_memory_id IN (SELECT value FROM json_each(?))
       ORDER BY relation, memory_id`,
    )
    .bind(list, list)
    .all<MemoryLinkView & { self: string }>();
  for (const { self, ...link } of rows.results) byId.get(self)?.push(link);
  return byId;
}
