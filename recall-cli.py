#!/usr/bin/env python3
"""Recall CLI — save and search reusable session entries.

Storage: ~/.claude/recall.db (SQLite + FTS5)
Schema is QuantifAI-compatible via session_id foreign key.
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".claude" / "recall.db"


def get_db() -> sqlite3.Connection:
    """Get or create the recall database."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _migrate(db)
    return db


def _migrate(db: sqlite3.Connection):
    """Apply schema migrations."""
    db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
    row = db.execute("SELECT version FROM schema_version").fetchone()
    version = row["version"] if row else 0

    if version < 1:
        db.executescript("""
            CREATE TABLE recipes (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL,
                project_path    TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                intent          TEXT NOT NULL,
                sources         TEXT,
                key_commands    TEXT,
                outcome         TEXT,
                prompt_template TEXT,
                tags            TEXT,
                quality_class   TEXT CHECK(quality_class IN
                                ('high_value', 'productive', 'neutral',
                                 'churn', 'dead_end')),
                quality_reason  TEXT,
                est_cost        REAL,
                token_count     INTEGER,
                outcome_ratio   REAL
            );

            CREATE VIRTUAL TABLE recipes_fts USING fts5(
                intent, outcome, prompt_template, tags,
                content=recipes, content_rowid=rowid
            );

            CREATE TRIGGER recipes_ai AFTER INSERT ON recipes BEGIN
                INSERT INTO recipes_fts(rowid, intent, outcome, prompt_template, tags)
                VALUES (new.rowid, new.intent, new.outcome, new.prompt_template, new.tags);
            END;

            CREATE TRIGGER recipes_au AFTER UPDATE ON recipes BEGIN
                INSERT INTO recipes_fts(recipes_fts, rowid, intent, outcome, prompt_template, tags)
                VALUES ('delete', old.rowid, old.intent, old.outcome, old.prompt_template, old.tags);
                INSERT INTO recipes_fts(rowid, intent, outcome, prompt_template, tags)
                VALUES (new.rowid, new.intent, new.outcome, new.prompt_template, new.tags);
            END;

            CREATE TRIGGER recipes_ad AFTER DELETE ON recipes BEGIN
                INSERT INTO recipes_fts(recipes_fts, rowid, intent, outcome, prompt_template, tags)
                VALUES ('delete', old.rowid, old.intent, old.outcome, old.prompt_template, old.tags);
            END;
        """)
        if version == 0:
            db.execute("INSERT INTO schema_version (version) VALUES (1)")
        else:
            db.execute("UPDATE schema_version SET version = 1")
        db.commit()


def cmd_save(args):
    """Save an entry to the database."""
    db = get_db()
    recipe_id = str(uuid.uuid4())[:8]

    # Calculate token count and cost from session if available
    token_count = None
    est_cost = None
    session_file = _find_session_file(args.session_id, args.project)
    if session_file:
        token_count, est_cost = _extract_session_cost(session_file)

    db.execute(
        """INSERT INTO recipes
           (id, session_id, project_path, intent, sources, key_commands,
            outcome, prompt_template, tags, quality_class, quality_reason,
            est_cost, token_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            recipe_id,
            args.session_id,
            args.project,
            args.intent,
            args.sources,
            args.key_commands,
            args.outcome,
            args.prompt_template,
            args.tags,
            args.quality_class,
            args.quality_reason,
            est_cost,
            token_count,
        ),
    )
    db.commit()
    db.close()

    print(json.dumps({
        "status": "saved",
        "id": recipe_id,
        "intent": args.intent,
        "quality_class": args.quality_class,
        "est_cost": est_cost,
        "token_count": token_count,
    }, indent=2))


def cmd_find(args):
    """Search entries by keyword using FTS5."""
    db = get_db()
    # Quote each term for FTS5 safety (hyphens, special chars)
    terms = args.query.split()
    # Use OR between terms for broader recall, AND is too strict
    query = " OR ".join(f'"{t}"' for t in terms)

    rows = db.execute(
        """SELECT r.id, r.intent, r.quality_class, r.tags, r.created_at,
                  r.project_path, r.est_cost,
                  snippet(recipes_fts, 0, '>>>', '<<<', '...', 30) as match_snippet
           FROM recipes_fts
           JOIN recipes r ON r.rowid = recipes_fts.rowid
           WHERE recipes_fts MATCH ?
           ORDER BY rank
           LIMIT 10""",
        (query,),
    ).fetchall()

    if not rows:
        print(json.dumps({"results": [], "query": query, "message": "No recipes found"}))
    else:
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "intent": r["intent"],
                "quality": r["quality_class"],
                "tags": r["tags"],
                "project": r["project_path"],
                "cost": r["est_cost"],
                "created": r["created_at"],
                "match": r["match_snippet"],
            })
        print(json.dumps({"results": results, "query": query}, indent=2))

    db.close()


def cmd_list(args):
    """List recent entries."""
    db = get_db()
    limit = getattr(args, "limit", 20)

    rows = db.execute(
        """SELECT id, intent, quality_class, tags, created_at, project_path, est_cost
           FROM recipes
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "intent": r["intent"],
            "quality": r["quality_class"],
            "tags": r["tags"],
            "project": r["project_path"],
            "cost": r["est_cost"],
            "created": r["created_at"],
        })

    print(json.dumps({"recipes": results, "count": len(results)}, indent=2))
    db.close()


def cmd_show(args):
    """Show full entry details."""
    db = get_db()

    row = db.execute(
        "SELECT * FROM recipes WHERE id = ? OR id LIKE ?",
        (args.id, f"{args.id}%"),
    ).fetchone()

    if not row:
        print(json.dumps({"error": f"Entry '{args.id}' not found"}))
        sys.exit(1)

    recipe = dict(row)
    # Parse JSON fields for display
    for field in ("sources", "key_commands", "tags"):
        if recipe.get(field):
            try:
                recipe[field] = json.loads(recipe[field])
            except (json.JSONDecodeError, TypeError):
                pass

    print(json.dumps(recipe, indent=2))
    db.close()


def cmd_use(args):
    """Output an entry's prompt template ready to paste, with variable hints."""
    db = get_db()

    row = db.execute(
        "SELECT id, intent, prompt_template, sources, key_commands, outcome, tags "
        "FROM recipes WHERE id = ? OR id LIKE ?",
        (args.id, f"{args.id}%"),
    ).fetchone()

    if not row:
        print(json.dumps({"error": f"Entry '{args.id}' not found"}))
        sys.exit(1)

    recipe = dict(row)

    # Parse template for variables, unescape literal \n
    import re
    template = (recipe.get("prompt_template") or "").replace("\\n", "\n")
    variables = re.findall(r"\{\{(\w+)\}\}", template)

    output = {
        "id": recipe["id"],
        "intent": recipe["intent"],
        "prompt_template": template,
        "variables": variables,
        "sources": recipe.get("sources"),
        "key_steps": recipe.get("key_commands"),
    }

    # If user provided variable values, substitute them
    if hasattr(args, "vars") and args.vars:
        filled = template
        for var_assignment in args.vars:
            if "=" in var_assignment:
                key, val = var_assignment.split("=", 1)
                filled = filled.replace("{{" + key + "}}", val)
        output["filled_prompt"] = filled

    print(json.dumps(output, indent=2))
    db.close()


def cmd_stats(args):
    """Show recall library statistics."""
    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    by_quality = db.execute(
        "SELECT quality_class, COUNT(*) as cnt FROM recipes GROUP BY quality_class ORDER BY cnt DESC"
    ).fetchall()
    by_project = db.execute(
        "SELECT project_path, COUNT(*) as cnt FROM recipes GROUP BY project_path ORDER BY cnt DESC"
    ).fetchall()
    total_cost = db.execute(
        "SELECT SUM(est_cost) FROM recipes WHERE est_cost IS NOT NULL"
    ).fetchone()[0]
    total_tokens = db.execute(
        "SELECT SUM(token_count) FROM recipes WHERE token_count IS NOT NULL"
    ).fetchone()[0]

    # Tag frequency
    all_tags = []
    rows = db.execute("SELECT tags FROM recipes WHERE tags IS NOT NULL").fetchall()
    for r in rows:
        try:
            all_tags.extend(json.loads(r[0]))
        except (json.JSONDecodeError, TypeError):
            pass

    from collections import Counter
    tag_freq = Counter(all_tags).most_common(10)

    output = {
        "total_recipes": total,
        "by_quality": {r[0]: r[1] for r in by_quality},
        "by_project": {r[0].split("/")[-1] if r[0] else "unknown": r[1] for r in by_project[:5]},
        "total_tracked_cost": round(total_cost, 4) if total_cost else 0,
        "total_tracked_tokens": total_tokens or 0,
        "top_tags": tag_freq,
        "db_path": str(DB_PATH),
    }

    print(json.dumps(output, indent=2))
    db.close()


def _find_session_file(session_id: str, project_path: str = None) -> Path | None:
    """Locate a session JSONL file."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None

    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        candidate = proj_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def _extract_session_cost(session_file: Path) -> tuple:
    """Extract total tokens and estimated cost from a session JSONL."""
    total_tokens = 0
    total_cost = 0.0

    # Approximate pricing per 1M tokens (input/output blended)
    MODEL_COSTS = {
        "claude-opus-4-6": 0.015,       # ~$15/M input, $75/M output blended
        "claude-sonnet-4-6": 0.003,      # ~$3/M input, $15/M output blended
        "claude-haiku-4-5": 0.0008,      # ~$0.8/M input, $4/M output blended
    }

    try:
        with open(session_file) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") != "assistant":
                    continue

                msg = obj.get("message", {})
                if not isinstance(msg, dict):
                    continue

                usage = msg.get("usage", {})
                model = msg.get("model", "")

                input_t = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                output_t = usage.get("output_tokens", 0)
                tokens = input_t + output_t
                total_tokens += tokens

                # Find matching cost rate
                rate = 0.003  # default to sonnet-class
                for model_key, model_rate in MODEL_COSTS.items():
                    if model_key in model:
                        rate = model_rate
                        break

                total_cost += tokens * rate / 1_000_000

    except Exception:
        pass

    return (total_tokens if total_tokens > 0 else None,
            round(total_cost, 4) if total_cost > 0 else None)


def main():
    parser = argparse.ArgumentParser(description="Recall CLI")
    sub = parser.add_subparsers(dest="command")

    # save
    save_p = sub.add_parser("save")
    save_p.add_argument("--session-id", required=True)
    save_p.add_argument("--project", default=None)
    save_p.add_argument("--intent", required=True)
    save_p.add_argument("--sources", default="[]")
    save_p.add_argument("--key-commands", default="[]")
    save_p.add_argument("--outcome", default=None)
    save_p.add_argument("--prompt-template", default=None)
    save_p.add_argument("--quality-class", default="productive",
                        choices=["high_value", "productive", "neutral", "churn", "dead_end"])
    save_p.add_argument("--quality-reason", default=None)
    save_p.add_argument("--tags", default="[]")

    # find
    find_p = sub.add_parser("find")
    find_p.add_argument("query")

    # list
    list_p = sub.add_parser("list")
    list_p.add_argument("--limit", type=int, default=20)

    # show
    show_p = sub.add_parser("show")
    show_p.add_argument("id")

    # use
    use_p = sub.add_parser("use")
    use_p.add_argument("id")
    use_p.add_argument("--var", dest="vars", action="append",
                       help="Variable substitution: --var key=value")

    # stats
    sub.add_parser("stats")

    args = parser.parse_args()

    if args.command == "save":
        cmd_save(args)
    elif args.command == "find":
        cmd_find(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "use":
        cmd_use(args)
    elif args.command == "stats":
        cmd_stats(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
