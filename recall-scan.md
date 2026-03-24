# /recall-scan — Batch scan sessions for recall-worthy patterns

Scan recent Claude Code sessions and identify high-value ones worth saving.

## Usage

- `/recall-scan` — Scan sessions from the last 30 days
- `/recall-scan 7` — Scan sessions from the last N days
- `/recall-scan all` — Scan all sessions across all projects

## Instructions

1. Run the scan script to find candidate sessions:

```bash
python3 ~/.claude/commands/recall-scan.py --days {{days_or_all}}
```

2. The script outputs candidate sessions with:
   - Session ID, project, date
   - First user prompt (intent)
   - Tools used count, estimated cost
   - Whether an entry already exists for this session

3. Review the candidates. For each one that looks worth saving, use `/recall save` logic to extract and save the entry. Focus on sessions that:
   - Produced commits (high_value signal)
   - Had low token count relative to output (efficient)
   - Share intent with other sessions (pattern signal)
   - Involved operational sequences (startup, deploy, migrate)

4. Skip sessions that are:
   - Pure exploration/reading with no actionable output
   - "Request interrupted" noise
   - Context continuation sessions (started with "This session is being continued...")
   - Very short (<5 messages)

5. After saving entries, report: how many sessions scanned, how many entries extracted, total library size.
