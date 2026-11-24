import { Miniflare } from "miniflare";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { linkMemories, linksFor, outcomeTally, reportOutcome, unlinkMemories } from "../src/graph";
import { searchMemories, setMemoryStatus } from "../src/index";
import { applyMigrations } from "./schema";

// Real local D1/FTS5 with synthetic records. No production credentials or data.
const runtime = new Miniflare({
  modules: true,
  script: "export default { fetch() { return new Response('test'); } }",
  compatibilityDate: "2026-07-29",
  d1Databases: ["DB", "LEGACY"],
});
let db: D1Database;

const id = (n: number) => `mem_${n.toString(16).padStart(32, "0")}`;

async function insert(
  target: D1Database,
  n: number,
  fields: { title: string; body: string; status?: string; updated?: string; related?: number },
): Promise<string> {
  await target
    .prepare(
      `INSERT INTO memories
         (id, stable_key, kind, title, body, status, source_client, source_machine,
          provenance_json, related_memory_id, updated_at)
       VALUES (?, ?, 'recipe', ?, ?, ?, 'codex', 'fixture', '{}', ?, ?)`,
    )
    .bind(
      id(n),
      `fixture:${n}`,
      fields.title,
      fields.body,
      fields.status ?? "Approved",
      fields.related === undefined ? null : id(fields.related),
      fields.updated ?? "2026-01-01 00:00:00",
    )
    .run();
  return id(n);
}

const report = (memory: string, outcome: "helped" | "misled", session: string, note?: string) =>
  reportOutcome(db, { id: memory, outcome, session_id: session, actor: "agent:test", note });

const ids = (rows: Record<string, unknown>[]) => rows.map((row) => row.id);

beforeAll(async () => {
  db = await runtime.getD1Database("DB");
  await applyMigrations(db);
}, 30_000);

afterAll(async () => {
  await runtime.dispose();
});

describe("outcome-weighted search", () => {
  it("moves a memory that helped above an equally relevant newer one", async () => {
    const older = await insert(db, 1, { title: "Alpha upload retry", body: "Resume alpha uploads.", updated: "2026-01-01 00:00:00" });
    const newer = await insert(db, 2, { title: "Alpha upload retry", body: "Resume alpha uploads.", updated: "2026-02-01 00:00:00" });
    expect(ids(await searchMemories({ DB: db }, "alpha", "Approved", undefined, 2))).toEqual([newer, older]);

    await report(older, "helped", "s1");
    const rows = await searchMemories({ DB: db }, "alpha", "Approved", undefined, 2);
    expect(ids(rows)).toEqual([older, newer]);
    expect(rows[0].outcomes).toEqual({ helped: 1, misled: 0 });
  });

  it("moves a memory that misled below an equally relevant older one", async () => {
    const older = await insert(db, 3, { title: "Bravo cache reset", body: "Clear bravo cache.", updated: "2026-01-01 00:00:00" });
    const newer = await insert(db, 4, { title: "Bravo cache reset", body: "Clear bravo cache.", updated: "2026-02-01 00:00:00" });
    await report(newer, "misled", "s1");
    expect(ids(await searchMemories({ DB: db }, "bravo", "Approved", undefined, 2))).toEqual([older, newer]);
  });

  it("keeps a much more relevant memory above a lightly boosted one", async () => {
    const relevant = await insert(db, 5, { title: "Charlie deploy charlie", body: "charlie charlie charlie" });
    const boosted = await insert(db, 6, {
      title: "General release notes",
      body: `Mentions charlie once among ${"unrelated words ".repeat(60)}`,
    });
    await report(boosted, "helped", "s1");
    await report(boosted, "helped", "s2");
    expect(ids(await searchMemories({ DB: db }, "charlie", "Approved", undefined, 2))).toEqual([relevant, boosted]);
  });

  it("applies the boost before the limit", async () => {
    const top = await insert(db, 7, { title: "Delta probe", body: "Delta probe steps.", updated: "2026-03-01 00:00:00" });
    const proven = await insert(db, 8, { title: "Delta probe", body: "Delta probe steps.", updated: "2026-01-01 00:00:00" });
    expect(ids(await searchMemories({ DB: db }, "delta", "Approved", undefined, 1))).toEqual([top]);
    await report(proven, "helped", "s1");
    expect(ids(await searchMemories({ DB: db }, "delta", "Approved", undefined, 1))).toEqual([proven]);
  });

  it("never pulls a Candidate into Approved results, however often it helped", async () => {
    const candidate = await insert(db, 9, { title: "Echo shortcut", body: "Echo shortcut.", status: "Candidate" });
    for (const session of ["s1", "s2", "s3", "s4", "s5"]) await report(candidate, "helped", session);
    expect(await searchMemories({ DB: db }, "echo", "Approved", undefined, 5)).toEqual([]);
  });
});

describe("outcome reports", () => {
  it("counts one report per memory, session and actor", async () => {
    const memory = await insert(db, 10, { title: "Foxtrot", body: "Foxtrot." });
    expect((await report(memory, "helped", "same")).ok).toBe(true);
    const repeat = await report(memory, "helped", "same");
    expect(repeat).toEqual({ ok: true, value: { recorded: false, helped: 1, misled: 0 } });
    await report(memory, "misled", "other");
    expect(await outcomeTally(db, memory)).toEqual({ helped: 1, misled: 1 });
  });

  it("rejects credential-shaped notes and unknown memories", async () => {
    const memory = await insert(db, 11, { title: "Golf", body: "Golf." });
    const secretShaped = "sk-" + "x".repeat(40);
    expect(await report(memory, "helped", "s1", `used ${secretShaped}`)).toEqual({
      ok: false,
      error: "credential-shaped values are not accepted",
    });
    expect(await report(id(999), "helped", "s1")).toEqual({ ok: false, error: "Memory not found" });
    expect(await outcomeTally(db, memory)).toEqual({ helped: 0, misled: 0 });
  });
});

describe("memory links", () => {
  const link = (from: string, to: string, relation = "requires") =>
    linkMemories(db, { from_id: from, to_id: to, relation, reason: "fixture link reason", actor: "agent:test" });

  it("shows a link from both ends with the neighbour's title and status", async () => {
    const a = await insert(db, 20, { title: "Hotel setup", body: "Hotel setup." });
    const b = await insert(db, 21, { title: "Hotel prerequisite", body: "Prerequisite.", status: "Stale" });
    expect((await link(a, b)).ok).toBe(true);
    expect(await link(a, b)).toEqual({ ok: true, value: { created: false, event_id: null } });

    const links = await linksFor(db, [a, b]);
    expect(links.get(a)).toEqual([
      expect.objectContaining({ relation: "requires", direction: "outgoing", memory_id: b, title: "Hotel prerequisite", status: "Stale" }),
    ]);
    expect(links.get(b)).toEqual([
      expect.objectContaining({ relation: "requires", direction: "incoming", memory_id: a, title: "Hotel setup" }),
    ]);
    const [row] = await searchMemories({ DB: db }, "hotel setup", "Approved", undefined, 1);
    expect(row.links).toEqual(links.get(a));
  });

  it("rejects self-links and missing memories with a clean error", async () => {
    const a = await insert(db, 22, { title: "India", body: "India." });
    expect(await link(a, a)).toEqual({ ok: false, error: "A memory cannot link to itself" });
    expect(await link(a, id(998))).toEqual({ ok: false, error: `Memory not found: ${id(998)}` });
  });

  it("records adding and removing a link in the memory history", async () => {
    const a = await insert(db, 23, { title: "Juliet", body: "Juliet." });
    const b = await insert(db, 24, { title: "Kilo", body: "Kilo." });
    await link(a, b, "conflicts_with");
    const removed = await unlinkMemories(db, {
      from_id: a, to_id: b, relation: "conflicts_with", reason: "fixture unlink reason", actor: "agent:test",
    });
    expect(removed.ok && removed.value.removed).toBe(true);
    const events = await db
      .prepare("SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY created_at, event_type")
      .bind(a)
      .all<{ event_type: string }>();
    expect(events.results.map((e) => e.event_type).sort()).toEqual(["link_added", "link_removed"]);
    expect((await linksFor(db, [a])).get(a)).toEqual([]);
  });

  it("lets only the Superseded status write and clear supersedes links", async () => {
    const old = await insert(db, 25, { title: "Lima old", body: "Old." });
    const replacement = await insert(db, 26, { title: "Lima new", body: "New." });
    const status = (s: string, related?: string) =>
      setMemoryStatus(db, { id: old, status: s, reason: "fixture status reason", actor: "human:test", related_memory_id: related });

    expect(await status("Superseded", old)).toEqual({ ok: false, error: "related_memory_id cannot be the memory itself" });
    expect((await status("Superseded", replacement)).ok).toBe(true);
    expect((await linksFor(db, [replacement])).get(replacement)).toEqual([
      expect.objectContaining({ relation: "supersedes", direction: "outgoing", memory_id: old }),
    ]);
    expect(await unlinkMemories(db, {
      from_id: replacement, to_id: old, relation: "supersedes", reason: "fixture unlink reason", actor: "agent:test",
    })).toEqual({ ok: false, error: expect.stringContaining("set_memory_status") });

    expect((await status("Approved")).ok).toBe(true);
    expect((await linksFor(db, [old])).get(old)).toEqual([]);
  });
});

describe("migration 0004 backfill", () => {
  it("links each replacement to the memory it superseded", async () => {
    const legacy = await runtime.getD1Database("LEGACY");
    await applyMigrations(legacy, (name) => !name.includes("0004"));
    const replacement = await insert(legacy, 30, { title: "Mike new", body: "New." });
    const old = await insert(legacy, 31, { title: "Mike old", body: "Old.", status: "Superseded", related: 30 });
    await insert(legacy, 32, { title: "Mike unrelated", body: "Unrelated." });
    await applyMigrations(legacy, (name) => name.includes("0004"));

    const links = await legacy
      .prepare("SELECT from_memory_id, to_memory_id, relation FROM memory_links")
      .all();
    expect(links.results).toEqual([{ from_memory_id: replacement, to_memory_id: old, relation: "supersedes" }]);
  });
});
