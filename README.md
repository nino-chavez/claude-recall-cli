# claude-recipe-cli

Save and search reusable Claude Code session recipes. Global slash commands backed by SQLite + FTS5.

## Install

**One-liner (clone + install):**

```bash
git clone https://github.com/nino-chavez/claude-recipe-cli.git ~/.claude/recipe-cli && bash ~/.claude/recipe-cli/install.sh
```

**Or via curl:**

```bash
curl -fsSL https://raw.githubusercontent.com/nino-chavez/claude-recipe-cli/main/install.sh | bash
```

This installs `/recipe` and `/recipe-scan` as global Claude Code slash commands available in every session.

## Usage

| Command | Description |
|---------|-------------|
| `/recipe save` | Extract a recipe from the current session |
| `/recipe find <query>` | Search saved recipes by keyword |
| `/recipe list` | Show recent recipes |
| `/recipe show <id>` | Show full recipe details |
| `/recipe use <id>` | Get the prompt template ready to use |
| `/recipe use <id> --var key=value` | Fill in template variables |
| `/recipe stats` | Library statistics |
| `/recipe-scan` | Scan recent sessions for recipe-worthy patterns |
| `/recipe-scan 7` | Scan last N days |
| `/recipe-scan all` | Scan all sessions |

## Automatic scanning (session-end hook)

By default, recipe scanning is manual. To automatically scan for recipe-worthy sessions every time a Claude Code session ends, add a `SessionEnd` hook to your global settings (`~/.claude/settings.json`):

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/commands/recipe-scan.py --days 1 --min-score 30 --limit 5 >> ~/.claude/recipe-scan.log 2>&1",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

This logs candidates to `~/.claude/recipe-scan.log` after each session. Review the log periodically and `/recipe save` the sessions worth keeping.

You can adjust `--min-score` (0-100, default 30) and `--days` to tune sensitivity.

## How it works

- Recipes are stored in `~/.claude/recall.db` (SQLite with FTS5 full-text search)
- `/recipe save` analyzes the current session transcript and extracts intent, tools used, outcome, and a reusable prompt template with `{{variable}}` placeholders
- `/recipe-scan` scores past sessions by efficiency, output, focus, and intent clarity to find sessions worth saving as recipes
- No dependencies beyond Python 3 stdlib + SQLite

## Update

```bash
git -C ~/.claude/recipe-cli pull
```

Symlinks mean updates take effect immediately.

## Uninstall

```bash
rm ~/.claude/commands/recipe.md ~/.claude/commands/recipe-cli.py
rm ~/.claude/commands/recipe-scan.md ~/.claude/commands/recipe-scan.py
rm -rf ~/.claude/recipe-cli
# Optionally remove the database: rm ~/.claude/recall.db
```

## License

MIT
