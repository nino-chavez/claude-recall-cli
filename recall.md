# /recall — Session Recall Manager

Save the current session as a reusable recall entry, or search past entries.

## Usage

- `/recall save` — Extract a recall entry from the current session
- `/recall find <query>` — Search saved entries by keyword
- `/recall list` — Show recent entries
- `/recall show <id>` — Show full entry details
- `/recall use <id>` — Get the prompt template ready to paste (with variable hints)
- `/recall use <id> --var key=value` — Get the prompt with variables filled in
- `/recall stats` — Show library statistics (counts, tags, cost)

## Instructions

When the user runs `/recall save`:

1. Read the current session's JSONL file. The session ID is available from the current conversation context. The session file is at `~/.claude/projects/<encoded-project-path>/<session-id>.jsonl`.

2. Extract the key information by analyzing the session transcript:
   - Filter to `type=user` messages for intent (especially the first one)
   - Filter to `type=assistant` messages with `tool_use` content blocks for actions taken
   - Identify the tools used, files touched, and commands run

3. Generate a structured entry with these fields:
   - **intent**: One sentence — what was the user trying to accomplish?
   - **sources**: JSON array of files, APIs, databases, or references consulted
   - **key_commands**: JSON array of the 3-5 most important tool calls (skip exploratory reads)
   - **outcome**: What was produced? Files created/modified, data generated
   - **prompt_template**: A reusable prompt with `{{variable}}` placeholders for parts that would change
   - **quality_class**: One of: `high_value`, `productive`, `neutral`, `churn`, `dead_end`
   - **quality_reason**: One sentence explaining the rating
   - **tags**: JSON array of 3-7 lowercase keywords

4. Run the save script:
   ```bash
   python3 ~/.claude/commands/recall-cli.py save \
     --session-id "<session-id>" \
     --project "<project-path>" \
     --intent "<intent>" \
     --sources '<json-array>' \
     --key-commands '<json-array>' \
     --outcome "<outcome>" \
     --prompt-template "<template>" \
     --quality-class "<class>" \
     --quality-reason "<reason>" \
     --tags '<json-array>'
   ```

5. Confirm to the user what was saved and show the entry ID.

When the user runs `/recall find <query>`:

```bash
python3 ~/.claude/commands/recall-cli.py find "<query>"
```

Display the results in a readable format with intent, quality class, tags, and date.

When the user runs `/recall list`:

```bash
python3 ~/.claude/commands/recall-cli.py list
```

When the user runs `/recall show <id>`:

```bash
python3 ~/.claude/commands/recall-cli.py show "<id>"
```

Display the full entry including the prompt template.

When the user runs `/recall use <id>` (with optional `--var key=value`):

```bash
python3 ~/.claude/commands/recall-cli.py use "<id>" [--var key=value ...]
```

Display the filled prompt template prominently. If there are unfilled `{{variables}}`, ask the user for values before proceeding. Once all variables are filled, ask: "Want me to run this now?" If yes, execute the filled prompt as if the user had typed it.

When the user runs `/recall stats`:

```bash
python3 ~/.claude/commands/recall-cli.py stats
```

Display a clean summary of: total entries, quality breakdown, top tags, total tracked cost.

When the user runs `/recall-scan` (or `/recall-scan N`):

```bash
python3 ~/.claude/commands/recall-scan.py --days <N|all> --min-score 30 --limit 15
```

Display candidates ranked by score. For each high-scoring candidate, offer to extract an entry using the `/recall save` workflow.
