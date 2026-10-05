/** Read-time identities: preserve stored project/provenance fields verbatim. */
export function projectIdentity(project: string): string {
  let path = project.trim().replace(/\/+$/, "");
  // Do not interpret URLs or arbitrary labels as local paths.
  if (path.includes("://") || path.split("/").includes("..")) return path;
  path = path.split("/.worktrees/")[0];
  path = path.replace(/^(?:\/Users\/[^/]+|\/home\/[^/]+)\//, "~/");
  const managed = /^~\/\.codex\/worktrees\/[^/]+\/([^/]+)$/.exec(path);
  if (managed) return managed[1];
  if (path === "~/.dotfiles") return ".dotfiles";
  return path.replace(/^~\/Workspace\/dev\//, "");
}

/** Resolve legacy short names only when the known scoped identity is unique. */
export function resolveProjectAliases(project: string, stored: string[]): string[] {
  const requested = projectIdentity(project);
  if (!requested) return [];
  const aliasable = (identity: string) => !identity.includes(":")
    && !identity.split("/").includes("..") && !/\s/.test(identity);
  if (!aliasable(requested)) return stored.filter(value => projectIdentity(value) === requested);
  const leaf = requested.split("/").at(-1)!;
  const entries = stored.map(value => ({ value, identity: projectIdentity(value) }))
    .filter(entry => aliasable(entry.identity));
  const scopes = new Set(
    entries.map(entry => entry.identity)
      .filter(identity => identity.includes("/") && identity.split("/").at(-1) === leaf),
  );
  if (requested.includes("/")) scopes.add(requested);
  if (!requested.includes("/") && scopes.size > 1) {
    throw new Error(`Ambiguous project '${requested}'; use a scoped project: ${[...scopes].sort().join(", ")}`);
  }
  const target = requested.includes("/") ? requested : [...scopes][0] ?? requested;
  return entries.filter(({ identity }) =>
    identity === target || (scopes.size <= 1 && identity === leaf),
  ).map(({ value }) => value);
}

export async function projectAliases(db: D1Database, project: string): Promise<string[]> {
  // All statuses participate in ambiguity detection; approval cannot change identity.
  const catalog = await db.prepare(
    "SELECT DISTINCT project FROM memories WHERE project IS NOT NULL LIMIT 1001",
  ).all<{ project: string }>();
  if (catalog.results.length > 1000) {
    throw new Error("Project catalog exceeds the alias-resolution limit; an indexed project registry is required.");
  }
  return resolveProjectAliases(project, catalog.results.map(row => row.project));
}
