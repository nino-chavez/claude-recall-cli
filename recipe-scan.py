#!/usr/bin/env python3
"""Batch scan Claude Code sessions for recipe-worthy patterns.

Analyzes session transcripts and identifies candidates based on:
- Session efficiency (tokens per tool call)
- Tool diversity (not just reads)
- Outcome signals (Write/Edit tools = produced something)
- Pattern signals (similar intent to other sessions)
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
RECALL_DB = Path.home() / ".claude" / "recall.db"

# Sessions starting with these are noise
NOISE_PREFIXES = [
    "[Request interrupted",
    "This session is being continued",
    "Caveat: The messages below",
    "<local-command-caveat>",
]


def get_existing_session_ids() -> set:
    """Get session IDs already saved as recipes."""
    if not RECALL_DB.exists():
        return set()
    db = sqlite3.connect(str(RECALL_DB))
    rows = db.execute("SELECT session_id FROM recipes").fetchall()
    db.close()
    return {r[0] for r in rows}


def analyze_session(session_file: Path) -> dict | None:
    """Extract key metrics from a session JSONL file."""
    tool_counts = Counter()
    files_written = []
    files_read = []
    user_prompts = []
    total_tokens = 0
    models = Counter()
    first_timestamp = None
    last_timestamp = None
    message_count = 0

    try:
        with open(session_file) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = obj.get("type")
                ts = obj.get("timestamp")

                if ts:
                    if first_timestamp is None:
                        first_timestamp = ts
                    last_timestamp = ts

                if t == "user":
                    message_count += 1
                    msg = obj.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        user_prompts.append(content.strip()[:500])
                    elif isinstance(content, list):
                        for c in content:
                            if c.get("type") == "text" and c.get("text", "").strip():
                                user_prompts.append(c["text"].strip()[:500])
                                break

                elif t == "assistant":
                    message_count += 1
                    msg = obj.get("message", {})
                    usage = msg.get("usage", {})
                    model = msg.get("model", "")
                    if model:
                        models[model] += 1

                    tokens = (
                        usage.get("input_tokens", 0)
                        + usage.get("output_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                    )
                    total_tokens += tokens

                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if c.get("type") == "tool_use":
                                name = c.get("name", "unknown")
                                tool_counts[name] += 1
                                inp = c.get("input", {})

                                if name in ("Write", "Edit"):
                                    fp = inp.get("file_path", "")
                                    if fp:
                                        files_written.append(fp)
                                elif name == "Read":
                                    fp = inp.get("file_path", "")
                                    if fp:
                                        files_read.append(fp)

    except Exception as e:
        return None

    if not user_prompts or message_count < 5:
        return None

    first_prompt = user_prompts[0]

    # Filter noise
    for prefix in NOISE_PREFIXES:
        if first_prompt.startswith(prefix):
            return None

    # Calculate efficiency metrics
    total_tools = sum(tool_counts.values())
    write_tools = tool_counts.get("Write", 0) + tool_counts.get("Edit", 0)
    has_output = write_tools > 0 or tool_counts.get("Bash", 0) > 0

    # Parse timestamps for duration
    duration_min = None
    if first_timestamp and last_timestamp:
        try:
            t1 = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
            duration_min = (t2 - t1).total_seconds() / 60
        except (ValueError, TypeError):
            pass

    return {
        "session_id": session_file.stem,
        "first_prompt": first_prompt,
        "prompt_count": len(user_prompts),
        "message_count": message_count,
        "total_tokens": total_tokens,
        "total_tools": total_tools,
        "write_tools": write_tools,
        "has_output": has_output,
        "tool_breakdown": dict(tool_counts.most_common(5)),
        "files_written": files_written[:10],
        "models": dict(models.most_common(3)),
        "duration_min": round(duration_min, 1) if duration_min else None,
        "file_size_kb": session_file.stat().st_size // 1024,
        "mtime": datetime.fromtimestamp(
            session_file.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def score_recipe_worthiness(session: dict) -> tuple[float, str]:
    """Score how likely a session is to contain a reusable recipe.
    Returns (score 0-100, reason). Higher differentiation than 0-10."""
    score = 0.0
    reasons = []

    # === Output signals (0-25 pts) ===
    write_tools = session["write_tools"]
    if write_tools > 0:
        # Scale by how much was produced
        if write_tools >= 10:
            score += 25.0
            reasons.append(f"heavy output ({write_tools} writes)")
        elif write_tools >= 3:
            score += 18.0
            reasons.append(f"solid output ({write_tools} writes)")
        else:
            score += 10.0
            reasons.append(f"some output ({write_tools} writes)")
    elif session["tool_breakdown"].get("Bash", 0) >= 3:
        score += 8.0
        reasons.append("operational (bash-heavy)")

    # === Efficiency (0-20 pts) ===
    if session["total_tokens"] > 0 and session["total_tools"] > 0:
        tokens_per_tool = session["total_tokens"] / session["total_tools"]
        if tokens_per_tool < 2000:
            score += 20.0
            reasons.append("very efficient")
        elif tokens_per_tool < 5000:
            score += 15.0
            reasons.append("efficient")
        elif tokens_per_tool < 20000:
            score += 8.0
            reasons.append("moderate efficiency")
        elif tokens_per_tool > 50000:
            score -= 5.0
            reasons.append("inefficient (high token burn)")

    # === Focus (0-15 pts) ===
    pc = session["prompt_count"]
    if 3 <= pc <= 8:
        score += 15.0
        reasons.append(f"focused ({pc} prompts)")
    elif 9 <= pc <= 15:
        score += 10.0
        reasons.append(f"moderate focus ({pc} prompts)")
    elif pc > 25:
        score -= 5.0
        reasons.append(f"unfocused ({pc} prompts)")

    # === Tool diversity (0-10 pts) ===
    tool_types = set(session["tool_breakdown"].keys())
    productive_tools = tool_types & {"Write", "Edit", "Bash", "Grep", "Glob"}
    if len(productive_tools) >= 4:
        score += 10.0
        reasons.append("full workflow")
    elif len(productive_tools) >= 3:
        score += 7.0
        reasons.append("diverse tools")
    elif len(productive_tools) >= 2:
        score += 4.0

    # === Time efficiency (0-15 pts) ===
    dur = session["duration_min"]
    if dur:
        if dur < 10 and write_tools > 0:
            score += 15.0
            reasons.append(f"quick win ({dur}m)")
        elif dur < 20 and write_tools > 0:
            score += 10.0
            reasons.append(f"fast ({dur}m)")
        elif dur < 30 and write_tools > 0:
            score += 5.0
        elif dur > 60:
            score -= 3.0
            reasons.append(f"long session ({dur}m)")

    # === Intent clarity (0-15 pts) ===
    intent = session["first_prompt"].lower()
    # Clear action verbs suggest reproducible tasks
    action_patterns = [
        ("fix ", 12), ("optimize ", 12), ("implement ", 10),
        ("convert ", 12), ("migrate ", 12), ("deploy ", 10),
        ("audit ", 12), ("address ", 10), ("create ", 8),
        ("set up ", 10), ("configure ", 10), ("build ", 8),
    ]
    for pattern, pts in action_patterns:
        if intent.startswith(pattern) or f" {pattern}" in intent:
            score += pts
            reasons.append(f"clear intent ({pattern.strip()})")
            break

    # Penalize vague/exploratory intents
    vague_patterns = ["check ", "look at ", "is this ", "can we ", "what "]
    for pattern in vague_patterns:
        if intent.startswith(pattern):
            score -= 5.0
            reasons.append("exploratory intent")
            break

    return max(0.0, min(100.0, score)), ", ".join(reasons)


def main():
    parser = argparse.ArgumentParser(description="Scan sessions for recipes")
    parser.add_argument(
        "--days",
        default="30",
        help="Number of days to look back, or 'all'",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=4.0,
        help="Minimum recipe-worthiness score (0-10)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max candidates to return",
    )
    args = parser.parse_args()

    existing = get_existing_session_ids()

    # Determine time cutoff
    if args.days == "all":
        cutoff = None
    else:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=int(args.days))

    candidates = []
    total_scanned = 0
    total_skipped_existing = 0
    total_skipped_noise = 0

    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue

        project_name = (
            str(proj_dir.name).replace("-Users-nino-", "").replace("-", "/")
        )

        for jf in proj_dir.glob("*.jsonl"):
            # Check mtime against cutoff
            if cutoff:
                mtime = datetime.fromtimestamp(
                    jf.stat().st_mtime, tz=timezone.utc
                )
                if mtime < cutoff:
                    continue

            total_scanned += 1

            # Skip if already a recipe
            if jf.stem in existing:
                total_skipped_existing += 1
                continue

            session = analyze_session(jf)
            if session is None:
                total_skipped_noise += 1
                continue

            session["project"] = project_name
            score, reason = score_recipe_worthiness(session)
            session["recipe_score"] = score
            session["score_reason"] = reason

            if score >= args.min_score:
                candidates.append(session)

    # Sort by score descending
    candidates.sort(key=lambda s: s["recipe_score"], reverse=True)
    candidates = candidates[: args.limit]

    output = {
        "scan_summary": {
            "total_scanned": total_scanned,
            "skipped_existing": total_skipped_existing,
            "skipped_noise": total_skipped_noise,
            "candidates_found": len(candidates),
            "min_score": args.min_score,
            "days": args.days,
        },
        "candidates": [
            {
                "session_id": c["session_id"],
                "project": c["project"],
                "intent": c["first_prompt"][:200],
                "score": c["recipe_score"],
                "reason": c["score_reason"],
                "tokens": c["total_tokens"],
                "tools": c["total_tools"],
                "writes": c["write_tools"],
                "duration_min": c["duration_min"],
                "prompts": c["prompt_count"],
                "date": c["mtime"][:10],
            }
            for c in candidates
        ],
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
