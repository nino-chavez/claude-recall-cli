# /recipe-scan — Batch scan sessions for recipe-worthy patterns

Scan recent Claude Code sessions and auto-extract recipes from high-value ones.

## Usage

- `/recipe-scan` — Scan sessions from the last 30 days
- `/recipe-scan 7` — Scan sessions from the last N days
- `/recipe-scan all` — Scan all sessions across all projects

## Instructions

1. Run the scan script to find candidate sessions:

```bash
python3 ~/.claude/commands/recipe-scan.py --days {{days_or_all}}
```

2. The script outputs candidate sessions with:
   - Session ID, project, date
   - First user prompt (intent)
   - Tools used count, estimated cost
   - Whether a recipe already exists for this session

3. Review the candidates. For each one that looks recipe-worthy, use `/recipe save` logic to extract and save the recipe. Focus on sessions that:
   - Produced commits (high_value signal)
   - Had low token count relative to output (efficient)
   - Share intent with other sessions (pattern signal)
   - Involved operational sequences (startup, deploy, migrate)

4. Skip sessions that are:
   - Pure exploration/reading with no actionable output
   - "Request interrupted" noise
   - Context continuation sessions (started with "This session is being continued...")
   - Very short (<5 messages)

5. After saving recipes, report: how many sessions scanned, how many recipes extracted, total library size.
