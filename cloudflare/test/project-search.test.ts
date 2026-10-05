import { Miniflare } from "miniflare";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { listMemoryCandidates, searchMemories, type Env } from "../src/index";
import { projectIdentity, resolveProjectAliases } from "../src/projects";
import schema from "../migrations/0001_init.sql?raw";

// Real local D1/FTS5, with synthetic records. No production credentials or data.
const runtime = new Miniflare({
  modules: true,
  script: "export default { fetch() { return new Response('test'); } }",
  compatibilityDate: "2026-07-29",
  d1Databases: ["DB"],
});
let env: Pick<Env, "DB">;

beforeAll(async () => {
  const DB = await runtime.getD1Database("DB");
  // exec accepts multiple statements when each statement occupies a single line.
  await DB.exec(schema.split(/\n\n+/).map(block => block.replace(/\n/g, " ")).join("\n"));
  env = { DB };
  const rows = [
    ["legacy", "rally-hq", "Approved"],
    ["mac-a", "/Users/nino/Workspace/dev/apps/rally-hq", "Approved"],
    ["mac-b", "/Users/nino.chavez/Workspace/dev/apps/rally-hq", "Approved"],
    ["worktree", "/Users/nino/Workspace/dev/apps/rally-hq/.worktrees/fix/courts", "Approved"],
    ["managed", "/Users/nino/.codex/worktrees/6101/rally-hq", "Approved"],
    ["candidate", "rally-hq", "Candidate"],
    ["stale", "rally-hq", "Stale"],
    ["superseded", "rally-hq", "Superseded"],
    ["contradicted", "rally-hq", "Contradicted"],
    ["other", "rally-hq-tools", "Approved"],
  ];
  for (const [id, project, status] of rows) {
    await DB.prepare(`INSERT INTO memories
      (id, stable_key, kind, title, body, project, status, source_client, source_machine, provenance_json)
      VALUES (?, ?, 'recipe', 'DataList correction', 'Verify court headers', ?, ?, 'codex', 'fixture', '{}')`)
      .bind(id, id, project, status).run();
  }
}, 30_000);

afterAll(async () => { await runtime.dispose(); });

describe("project-scoped memory retrieval", () => {
  it.each([
    "rally-hq",
    "apps/rally-hq",
    "/Users/nino/Workspace/dev/apps/rally-hq",
    "/Users/nino.chavez/Workspace/dev/apps/rally-hq",
    "/Users/nino/Workspace/dev/apps/rally-hq/.worktrees/fix/courts",
    "/Users/nino/.codex/worktrees/6101/rally-hq",
  ])("retrieves the same approved memories for %s", async (project) => {
    const rows = await searchMemories(env, "DataList", "Approved", project, 25);
    expect(rows.map(row => row.id).sort()).toEqual(["legacy", "mac-a", "mac-b", "managed", "worktree"]);
  });

  it("keeps the review queue separate and filters before applying the limit", async () => {
    const rows = await searchMemories(env, "DataList", "Candidate", "apps/rally-hq", 1);
    expect(rows.map(row => row.id)).toEqual(["candidate"]);
  });

  it("uses the same aliases when listing candidates", async () => {
    expect((await listMemoryCandidates(env, "apps/rally-hq", 1)).map(row => row.id)).toEqual(["candidate"]);
    expect(await listMemoryCandidates(env, "%", 25)).toEqual([]);
  });

  it("does not interpret SQL wildcard project names as broad searches", async () => {
    expect(await searchMemories(env, "DataList", "Approved", "%", 25)).toEqual([]);
  });

  it("preserves unscoped search", async () => {
    expect(await searchMemories(env, "DataList", "Approved", undefined, 25)).toHaveLength(6);
  });

  it("keeps stored project values unchanged", async () => {
    const rows = await searchMemories(env, "DataList", "Approved", "apps/rally-hq", 25);
    expect(rows.find(row => row.id === "mac-b")?.project).toBe("/Users/nino.chavez/Workspace/dev/apps/rally-hq");
  });

  it("detects collisions across statuses without mixing their memories", async () => {
    for (const [id, project, status] of [
      ["demo-app", "apps/demo", "Approved"],
      ["demo-site", "sites/demo", "Candidate"],
      ["demo-short", "demo", "Approved"],
    ]) {
      await env.DB.prepare(`INSERT INTO memories
        (id, stable_key, kind, title, body, project, status, source_client, source_machine, provenance_json)
        VALUES (?, ?, 'recipe', 'Collision case', 'Synthetic collision', ?, ?, 'codex', 'fixture', '{}')`)
        .bind(id, id, project, status).run();
    }
    await expect(searchMemories(env, "Collision", "Approved", "demo", 25)).rejects.toThrow(/Ambiguous project/);
    await expect(listMemoryCandidates(env, "demo", 25)).rejects.toThrow(/Ambiguous project/);
    expect((await searchMemories(env, "Collision", "Approved", "apps/demo", 25)).map(row => row.id))
      .toEqual(["demo-app"]);
    expect((await listMemoryCandidates(env, "sites/demo", 25)).map(row => row.id)).toEqual(["demo-site"]);
  });

  it("rejects an oversized project catalog instead of resolving a partial one", async () => {
    const projects = Array.from({ length: 1001 }, (_, i) => `catalog-project-${i}`);
    await env.DB.prepare(`INSERT INTO memories
      (id, stable_key, kind, title, body, project, status, source_client, source_machine, provenance_json)
      SELECT value, value, 'recipe', 'Catalog fixture', 'Synthetic', value, 'Candidate',
        'codex', 'catalog-limit-fixture', '{}' FROM json_each(?)`)
      .bind(JSON.stringify(projects)).run();
    try {
      await expect(searchMemories(env, "DataList", "Approved", "rally-hq", 25))
        .rejects.toThrow(/catalog exceeds/);
      await expect(listMemoryCandidates(env, "rally-hq", 25)).rejects.toThrow(/catalog exceeds/);
      expect(await searchMemories(env, "DataList", "Approved", undefined, 25)).toHaveLength(6);
    } finally {
      await env.DB.prepare("DELETE FROM memories WHERE source_machine = 'catalog-limit-fixture'").run();
    }
  });
});

describe("project identity boundaries", () => {
  it.each(["/Users/nino/.dotfiles", "/Users/nino.chavez/.dotfiles", "~/.dotfiles", ".dotfiles"])(
    "recognizes dotfiles across home directories: %s", project => {
      expect(projectIdentity(project)).toBe(".dotfiles");
    },
  );

  const projects = ["apps/demo", "sites/demo", "demo", "apps/demo-tools"];
  it("requires an explicit scope for colliding short names", () => {
    expect(() => resolveProjectAliases("demo", projects)).toThrow(/Ambiguous project/);
  });
  it("does not assign an ambiguous legacy slug to either scoped project", () => {
    expect(resolveProjectAliases("apps/demo", projects)).toEqual(["apps/demo"]);
    expect(resolveProjectAliases("sites/demo", projects)).toEqual(["sites/demo"]);
  });
  it("does not alias a requested different scope to another project's short name", () => {
    expect(resolveProjectAliases("sites/demo", ["apps/demo", "demo"])).toEqual([]);
  });
  it("does not collapse arbitrary URLs, traversal paths, or labels", () => {
    expect(resolveProjectAliases("https://example.com/demo", ["demo"])).toEqual([]);
    expect(resolveProjectAliases("apps/../demo", ["demo"])).toEqual([]);
    expect(resolveProjectAliases("github.com/owner (account-wide)", ["owner (account-wide)"])).toEqual([]);
  });
  it("keeps blank filters closed", async () => {
    expect(await searchMemories(env, "DataList", "Approved", "  ", 25)).toEqual([]);
  });
});
