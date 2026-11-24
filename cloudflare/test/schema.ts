/// <reference types="vite/client" />
// Applies every migration in order, the way `wrangler d1 migrations apply` does.
const migrations = import.meta.glob<string>("../migrations/*.sql", {
  query: "?raw",
  import: "default",
  eager: true,
});

export const MIGRATION_NAMES = Object.keys(migrations).sort();

// D1 exec runs one statement per line. Drop comment lines, then fold each
// blank-line-separated block (one statement or one trigger) onto a single line.
export function flattenForExec(sql: string): string {
  return sql
    .split("\n")
    .filter((line) => !line.trim().startsWith("--"))
    .join("\n")
    .split(/\n\s*\n+/)
    .map((block) => block.replace(/\s*\n\s*/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

export async function applyMigrations(
  db: D1Database,
  include: (name: string) => boolean = () => true,
): Promise<void> {
  for (const name of MIGRATION_NAMES.filter(include)) {
    await db.exec(flattenForExec(migrations[name]));
  }
}
