# claude-recall-cli

Save and search reusable Claude Code session entries. Global slash commands backed by SQLite + FTS5.

## Install

**One-liner (clone + install):**

```bash
git clone https://github.com/nino-chavez/claude-recall-cli.git ~/.claude/recall-cli && bash ~/.claude/recall-cli/install.sh
```

**Or via curl:**

```bash
curl -fsSL https://raw.githubusercontent.com/nino-chavez/claude-recall-cli/main/install.sh | bash
```

This installs `/recall` and `/recall-scan` as global Claude Code slash commands available in every session.

## Usage

| Command | Description |
|---------|-------------|
| `/recall save` | Extract an entry from the current session |
| `/recall find <query>` | Search saved entries by keyword |
| `/recall list` | Show recent entries |
| `/recall show <id>` | Show full entry details |
| `/recall use <id>` | Get the prompt template ready to use |
| `/recall use <id> --var key=value` | Fill in template variables |
| `/recall stats` | Library statistics |
| `/recall analyze` | Analyze a session for quality patterns |
| `/recall quality` | Quality trends across recent sessions |
| `/recall quality --days 7` | Quality trends for last N days |
| `/recall-scan` | Scan recent sessions for recall-worthy patterns |
| `/recall-scan 7` | Scan last N days |
| `/recall-scan all` | Scan all sessions |

## Automatic scanning (session-end hook)

By default, scanning is manual. To automatically scan for recall-worthy sessions every time a Claude Code session ends, add a `SessionEnd` hook to your global settings (`~/.claude/settings.json`):

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/commands/recall-scan.py --days 1 --min-score 30 --limit 5 >> ~/.claude/recall-scan.log 2>&1",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

This logs candidates to `~/.claude/recall-scan.log` after each session. Review the log periodically and `/recall save` the sessions worth keeping.

You can adjust `--min-score` (0-100, default 30) and `--days` to tune sensitivity.

## How it works

- Entries are stored in `~/.claude/recall.db` (SQLite with FTS5 full-text search)
- `/recall save` analyzes the current session transcript and extracts intent, tools used, outcome, and a reusable prompt template with `{{variable}}` placeholders
- `/recall-scan` scores past sessions by efficiency, output, focus, and intent clarity to find sessions worth saving
- No dependencies beyond Python 3 stdlib + SQLite

## Update

```bash
git -C ~/.claude/recall-cli pull
```

Symlinks mean updates take effect immediately.

## Uninstall

```bash
rm ~/.claude/commands/recall.md ~/.claude/commands/recall-cli.py
rm ~/.claude/commands/recall-scan.md ~/.claude/commands/recall-scan.py
rm -rf ~/.claude/recall-cli
# Optionally remove the database: rm ~/.claude/recall.db
```

## License

MIT
