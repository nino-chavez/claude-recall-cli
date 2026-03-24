#!/usr/bin/env python3
"""Recall CLI — save and search reusable session entries.

Storage: ~/.claude/recall.db (SQLite + FTS5)
Schema is QuantifAI-compatible via session_id foreign key.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path.home() / ".claude" / "recall.db"

# Heuristic version — bump when scoring rules, patterns, or thresholds change.
# Stored alongside quality reports so scores are comparable only within
# the same version.
HEURISTIC_VERSION = 2

# Config file locations — co-located with this script
_SCRIPT_DIR = Path(__file__).resolve().parent
BASELINE_PATH = _SCRIPT_DIR / "baseline.json"
THRESHOLDS_PATH = _SCRIPT_DIR / "thresholds.json"

# Cached config (loaded once per invocation)
_baseline = None
_thresholds = None


def _load_baseline() -> dict:
    """Load compliance baseline derived from Claude Code system prompt."""
    global _baseline
    if _baseline is None:
        try:
            with open(BASELINE_PATH) as f:
                _baseline = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _baseline = {}
    return _baseline


def _load_thresholds() -> dict:
    """Load user-tunable efficiency thresholds."""
    global _thresholds
    if _thresholds is None:
        try:
            with open(THRESHOLDS_PATH) as f:
                _thresholds = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _thresholds = {}
    return _thresholds


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


# ---------------------------------------------------------------------------
# Session quality analysis
#
# Two layers:
#   1. COMPLIANCE — did Claude follow its own system prompt rules?
#      Sourced from baseline.json (derived from Anthropic's published prompt).
#   2. EFFICIENCY — did it do so within acceptable cost/effort bounds?
#      Sourced from thresholds.json (user-tunable, subjective).
# ---------------------------------------------------------------------------


def _parse_session_events(session_file: Path) -> list[dict]:
    """Parse a session JSONL into a list of typed events."""
    events = []
    try:
        with open(session_file) as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return events


def _analyze_tool_selection(events: list[dict]) -> dict:
    """COMPLIANCE: Check for Bash commands that should have used dedicated tools.
    Rules sourced from baseline.json → tool_selection."""
    baseline = _load_baseline()
    thresholds = _load_thresholds()

    ts = baseline.get("tool_selection", {})
    misuse_patterns = [
        (p["bash_pattern"], p["expected_tool"], p.get("prompt_rule", ""))
        for p in ts.get("misuse_patterns", [])
    ]
    bash_ok = tuple(ts.get("bash_ok_prefixes", []))
    penalty = thresholds.get("compliance_penalty_per_misuse", 15)

    misuses = []
    bash_count = 0

    for ev in events:
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use" or block.get("name") != "Bash":
                continue
            bash_count += 1
            cmd = block.get("input", {}).get("command", "").strip()

            if any(cmd.startswith(p) for p in bash_ok):
                continue

            for pattern, better_tool, rule in misuse_patterns:
                if re.search(pattern, cmd):
                    misuses.append({
                        "command": cmd[:120],
                        "should_use": better_tool,
                        "rule": rule,
                    })
                    break

    return {
        "layer": "compliance",
        "bash_calls": bash_count,
        "misuses": misuses,
        "misuse_count": len(misuses),
        "score": max(0, 100 - (len(misuses) * penalty)),
    }


def _analyze_thrash(events: list[dict]) -> dict:
    """EFFICIENCY: Detect re-edits of the same file (planning vs thrashing).
    Thresholds sourced from thresholds.json → thrash."""
    t = _load_thresholds().get("thrash", {})
    penalties = t.get("penalty_per_tier", {})

    edit_targets = []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use":
                continue
            if block.get("name") in ("Write", "Edit"):
                fp = block.get("input", {}).get("file_path", "")
                if fp:
                    edit_targets.append(fp)

    if not edit_targets:
        return {"layer": "efficiency", "edits": 0, "unique_files": 0,
                "re_edits": {}, "thrash_ratio": 0.0, "score": 100}

    counts = Counter(edit_targets)
    re_edits = {f: c for f, c in counts.items() if c > 2}
    unique = len(counts)
    total = len(edit_targets)
    thrash_ratio = round(total / unique, 2) if unique > 0 else 0.0

    score = 100
    if thrash_ratio > t.get("ratio_critical", 3.0):
        score -= penalties.get("critical", 40)
    elif thrash_ratio > t.get("ratio_high", 2.0):
        score -= penalties.get("high", 20)
    elif thrash_ratio > t.get("ratio_warning", 1.5):
        score -= penalties.get("warning", 10)

    for f, c in re_edits.items():
        if c >= t.get("single_file_max", 5):
            score -= penalties.get("single_file_over_max", 10)

    return {
        "layer": "efficiency",
        "edits": total,
        "unique_files": unique,
        "re_edits": {Path(f).name: c for f, c in re_edits.items()},
        "thrash_ratio": thrash_ratio,
        "score": max(0, score),
    }


def _analyze_prompt_clarity(events: list[dict]) -> dict:
    """EFFICIENCY: Measure how many user messages before first productive output.
    Thresholds sourced from thresholds.json → prompt_clarity."""
    t = _load_thresholds().get("prompt_clarity", {})

    user_msgs_before_output = 0
    total_user_msgs = 0
    found_output = False

    for ev in events:
        if ev.get("type") == "user":
            total_user_msgs += 1
            if not found_output:
                user_msgs_before_output += 1

        if ev.get("type") == "assistant" and not found_output:
            content = ev.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_use" and block.get("name") in ("Write", "Edit"):
                    found_output = True
                    break

    if not found_output:
        score = t.get("no_output_score", 30)
    elif user_msgs_before_output <= t.get("perfect_prompts", 1):
        score = 100
    elif user_msgs_before_output <= t.get("good_prompts", 2):
        score = 90
    elif user_msgs_before_output <= t.get("ok_prompts", 4):
        score = 75
    elif total_user_msgs > 0:
        warmup_ratio = user_msgs_before_output / total_user_msgs
        if warmup_ratio < t.get("warmup_ratio_good", 0.15):
            score = 70
        elif warmup_ratio < t.get("warmup_ratio_ok", 0.30):
            score = 55
        elif warmup_ratio < t.get("warmup_ratio_poor", 0.50):
            score = 40
        else:
            score = 25
    else:
        score = 20

    return {
        "layer": "efficiency",
        "prompts_before_output": user_msgs_before_output,
        "total_prompts": total_user_msgs,
        "had_output": found_output,
        "score": score,
    }


def _analyze_cost_efficiency(events: list[dict]) -> dict:
    """EFFICIENCY: Tokens per productive tool call (Write/Edit/Bash).
    Thresholds sourced from thresholds.json → cost_efficiency."""
    t = _load_thresholds().get("cost_efficiency", {})
    model_costs = t.get("model_costs_per_million", {
        "claude-opus-4-6": 0.015,
        "claude-sonnet-4-6": 0.003,
        "claude-haiku-4-5": 0.0008,
    })

    total_tokens = 0
    productive_calls = 0
    total_cost = 0.0

    for ev in events:
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message", {})
        if not isinstance(msg, dict):
            continue

        usage = msg.get("usage", {})
        model = msg.get("model", "")
        tokens = (usage.get("input_tokens", 0)
                  + usage.get("output_tokens", 0)
                  + usage.get("cache_read_input_tokens", 0))
        total_tokens += tokens

        rate = 0.003
        for model_key, model_rate in model_costs.items():
            if model_key in model:
                rate = model_rate
                break
        total_cost += tokens * rate / 1_000_000

        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use" and block.get("name") in ("Write", "Edit", "Bash"):
                    productive_calls += 1

    tokens_per_output = (
        round(total_tokens / productive_calls)
        if productive_calls > 0 else None
    )

    if tokens_per_output is None:
        score = t.get("no_output_score", 30)
    elif tokens_per_output < t.get("excellent", 3000):
        score = 100
    elif tokens_per_output < t.get("good", 8000):
        score = 80
    elif tokens_per_output < t.get("moderate", 20000):
        score = 60
    elif tokens_per_output < t.get("poor", 50000):
        score = 40
    else:
        score = 20

    return {
        "layer": "efficiency",
        "total_tokens": total_tokens,
        "est_cost": round(total_cost, 4),
        "productive_calls": productive_calls,
        "tokens_per_output": tokens_per_output,
        "score": score,
    }


def _analyze_antipatterns(events: list[dict]) -> dict:
    """COMPLIANCE: Detect behavioral anti-patterns defined in system prompt.
    Rules sourced from baseline.json → anti_patterns."""
    baseline = _load_baseline()
    thresholds = _load_thresholds()
    severity_weights = thresholds.get("anti_pattern_severity_weights",
                                      {"high": 25, "medium": 15, "low": 5})

    rules = {r["id"]: r for r in baseline.get("anti_patterns", {}).get("rules", [])}
    issues = []

    # Extract all tool calls in order
    tool_calls = []
    bash_cmds = []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            inp = block.get("input", {})
            tool_calls.append({"name": name, "input": inp})
            if name == "Bash":
                bash_cmds.append(inp.get("command", ""))

    # 1. Repeated identical Bash commands (no_retry_loops)
    rule = rules.get("no_retry_loops", {})
    threshold = rule.get("threshold", 3)
    bash_counts = Counter(bash_cmds)
    repeated = {c[:80]: n for c, n in bash_counts.items() if n >= threshold and c}
    if repeated:
        issues.append({
            "type": "repeated_commands",
            "rule": rule.get("prompt_rule", ""),
            "detail": f"{len(repeated)} commands run {threshold}+ times",
            "examples": list(repeated.keys())[:3],
            "severity": rule.get("severity", "high"),
        })

    # 2. Long read sequences with no output (no_brute_force)
    rule = rules.get("no_brute_force", {})
    threshold = rule.get("threshold", 15)
    consecutive_reads = 0
    max_reads_without_output = 0
    for tc in tool_calls:
        if tc["name"] in ("Read", "Grep", "Glob"):
            consecutive_reads += 1
        elif tc["name"] in ("Write", "Edit", "Bash"):
            max_reads_without_output = max(max_reads_without_output, consecutive_reads)
            consecutive_reads = 0
    max_reads_without_output = max(max_reads_without_output, consecutive_reads)

    if max_reads_without_output >= threshold:
        issues.append({
            "type": "exploration_dead_end",
            "rule": rule.get("prompt_rule", ""),
            "detail": f"{max_reads_without_output} consecutive reads without output",
            "severity": rule.get("severity", "medium"),
        })

    # 3. Excessive agent spawns (no_excessive_agents)
    rule = rules.get("no_excessive_agents", {})
    threshold = rule.get("threshold", 8)
    agent_count = sum(1 for tc in tool_calls if tc["name"] == "Agent")
    if agent_count >= threshold:
        issues.append({
            "type": "excessive_agents",
            "rule": rule.get("prompt_rule", ""),
            "detail": f"{agent_count} sub-agents spawned",
            "severity": rule.get("severity", "low"),
        })

    # 4. Edit without prior Read (read_before_edit)
    rule = rules.get("read_before_edit", {})
    if rule:
        files_read = set()
        edits_without_read = []
        for tc in tool_calls:
            if tc["name"] == "Read":
                fp = tc["input"].get("file_path", "")
                if fp:
                    files_read.add(fp)
            elif tc["name"] == "Edit":
                fp = tc["input"].get("file_path", "")
                if fp and fp not in files_read:
                    edits_without_read.append(Path(fp).name)
        if edits_without_read:
            issues.append({
                "type": "edit_without_read",
                "rule": rule.get("prompt_rule", ""),
                "detail": f"{len(edits_without_read)} files edited without reading first",
                "examples": edits_without_read[:5],
                "severity": rule.get("severity", "medium"),
            })

    score = 100 - sum(
        severity_weights.get(i["severity"], 0) for i in issues
    )

    return {
        "layer": "compliance",
        "issues": issues,
        "issue_count": len(issues),
        "score": max(0, score),
    }


def _run_analysis(session_file: Path) -> dict:
    """Run all analysis passes on a session file.

    Returns scores split into two layers:
      - compliance: tool_selection, anti_patterns (from baseline.json)
      - efficiency: planning, prompt_clarity, cost_efficiency (from thresholds.json)
    """
    events = _parse_session_events(session_file)
    if not events:
        return {"error": "No events found in session file"}

    tool_sel = _analyze_tool_selection(events)
    thrash = _analyze_thrash(events)
    clarity = _analyze_prompt_clarity(events)
    cost = _analyze_cost_efficiency(events)
    anti = _analyze_antipatterns(events)

    compliance_scores = {
        "tool_selection": tool_sel["score"],
        "anti_patterns": anti["score"],
    }
    efficiency_scores = {
        "planning": thrash["score"],
        "prompt_clarity": clarity["score"],
        "cost_efficiency": cost["score"],
    }
    all_scores = {**compliance_scores, **efficiency_scores}
    overall = round(sum(all_scores.values()) / len(all_scores), 1)
    compliance_avg = round(sum(compliance_scores.values()) / len(compliance_scores), 1)
    efficiency_avg = round(sum(efficiency_scores.values()) / len(efficiency_scores), 1)

    # Grade from thresholds
    grades = _load_thresholds().get("grades", {"A": 85, "B": 70, "C": 55, "D": 40})
    if overall >= grades.get("A", 85):
        grade = "A"
    elif overall >= grades.get("B", 70):
        grade = "B"
    elif overall >= grades.get("C", 55):
        grade = "C"
    elif overall >= grades.get("D", 40):
        grade = "D"
    else:
        grade = "F"

    return {
        "heuristic_version": HEURISTIC_VERSION,
        "baseline_source": "Claude Code system prompt (via baseline.json)",
        "overall_score": overall,
        "grade": grade,
        "compliance_score": compliance_avg,
        "efficiency_score": efficiency_avg,
        "scores": all_scores,
        "tool_selection": tool_sel,
        "thrash_analysis": thrash,
        "prompt_clarity": clarity,
        "cost_efficiency": cost,
        "anti_patterns": anti,
    }


def cmd_analyze(args):
    """Analyze a session for quality patterns."""
    session_file = None

    if args.session_id:
        session_file = _find_session_file(args.session_id)
    elif args.file:
        session_file = Path(args.file)

    if not session_file or not session_file.exists():
        print(json.dumps({"error": "Session file not found"}))
        sys.exit(1)

    result = _run_analysis(session_file)
    result["session_id"] = session_file.stem
    print(json.dumps(result, indent=2))


def cmd_quality(args):
    """Generate quality trends across recent sessions."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        print(json.dumps({"error": "No projects directory found"}))
        sys.exit(1)

    days = int(args.days) if args.days != "all" else None
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=days)
        if days else None
    )

    sessions = []
    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir():
            continue
        project_name = str(proj_dir.name).replace("-Users-nino-", "").replace("-", "/")

        for jf in proj_dir.glob("*.jsonl"):
            if cutoff:
                mtime = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    continue

            # Skip tiny sessions
            if jf.stat().st_size < 5000:
                continue

            result = _run_analysis(jf)
            if "error" in result:
                continue

            sessions.append({
                "session_id": jf.stem,
                "project": project_name,
                "date": datetime.fromtimestamp(
                    jf.stat().st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "grade": result["grade"],
                "overall": result["overall_score"],
                "compliance": result["compliance_score"],
                "efficiency": result["efficiency_score"],
                "scores": result["scores"],
                "tokens": result["cost_efficiency"]["total_tokens"],
                "cost": result["cost_efficiency"]["est_cost"],
                "misuses": result["tool_selection"]["misuse_count"],
                "thrash_ratio": result["thrash_analysis"]["thrash_ratio"],
                "issues": result["anti_patterns"]["issue_count"],
            })

    if not sessions:
        print(json.dumps({"error": "No sessions found in timeframe"}))
        sys.exit(1)

    sessions.sort(key=lambda s: s["date"], reverse=True)
    sessions = sessions[:args.limit]

    # Aggregate stats
    all_scores = [s["overall"] for s in sessions]
    grade_dist = Counter(s["grade"] for s in sessions)
    total_cost = sum(s["cost"] for s in sessions)
    total_tokens = sum(s["tokens"] for s in sessions)
    avg_score = round(sum(all_scores) / len(all_scores), 1)

    # Category averages
    cat_totals = {"tool_selection": 0, "planning": 0, "prompt_clarity": 0,
                  "cost_efficiency": 0, "anti_patterns": 0}
    for s in sessions:
        for cat, val in s["scores"].items():
            cat_totals[cat] += val
    cat_avgs = {k: round(v / len(sessions), 1) for k, v in cat_totals.items()}

    # Weakest category
    weakest = min(cat_avgs, key=cat_avgs.get)

    # Bottom 5 sessions
    worst = sorted(sessions, key=lambda s: s["overall"])[:5]

    # Layer averages
    avg_compliance = round(
        sum(s["compliance"] for s in sessions) / len(sessions), 1
    )
    avg_efficiency = round(
        sum(s["efficiency"] for s in sessions) / len(sessions), 1
    )

    output = {
        "summary": {
            "heuristic_version": HEURISTIC_VERSION,
            "baseline_source": "Claude Code system prompt (via baseline.json)",
            "sessions_analyzed": len(sessions),
            "days": args.days,
            "avg_score": avg_score,
            "avg_compliance": avg_compliance,
            "avg_efficiency": avg_efficiency,
            "grade_distribution": dict(grade_dist.most_common()),
            "total_cost": round(total_cost, 4),
            "total_tokens": total_tokens,
            "category_averages": cat_avgs,
            "weakest_category": weakest,
        },
        "worst_sessions": [
            {
                "session_id": s["session_id"][:12],
                "project": s["project"].split("/")[-1],
                "date": s["date"],
                "grade": s["grade"],
                "score": s["overall"],
                "issues": s["issues"],
            }
            for s in worst
        ],
        "recent": [
            {
                "session_id": s["session_id"][:12],
                "project": s["project"].split("/")[-1],
                "date": s["date"],
                "grade": s["grade"],
                "score": s["overall"],
            }
            for s in sessions[:10]
        ],
    }

    print(json.dumps(output, indent=2))


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

    # analyze
    analyze_p = sub.add_parser("analyze")
    analyze_g = analyze_p.add_mutually_exclusive_group(required=True)
    analyze_g.add_argument("--session-id", default=None,
                           help="Session ID to analyze")
    analyze_g.add_argument("--file", default=None,
                           help="Path to session JSONL file")

    # quality
    quality_p = sub.add_parser("quality")
    quality_p.add_argument("--days", default="30",
                           help="Days to look back, or 'all'")
    quality_p.add_argument("--limit", type=int, default=50,
                           help="Max sessions to analyze")

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
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "quality":
        cmd_quality(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
