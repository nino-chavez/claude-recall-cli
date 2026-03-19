# /recipe — Session Recipe Manager

Save the current session as a reusable recipe, or search past recipes.

## Usage

- `/recipe save` — Extract a recipe from the current session
- `/recipe find <query>` — Search saved recipes by keyword
- `/recipe list` — Show recent recipes
- `/recipe show <id>` — Show full recipe details
- `/recipe use <id>` — Get the prompt template ready to paste (with variable hints)
- `/recipe use <id> --var key=value` — Get the prompt with variables filled in
- `/recipe stats` — Show library statistics (counts, tags, cost)

## Instructions

When the user runs `/recipe save`:

1. Read the current session's JSONL file. The session ID is available from the current conversation context. The session file is at `~/.claude/projects/<encoded-project-path>/<session-id>.jsonl`.

2. Extract the key information by analyzing the session transcript:
   - Filter to `type=user` messages for intent (especially the first one)
   - Filter to `type=assistant` messages with `tool_use` content blocks for actions taken
   - Identify the tools used, files touched, and commands run

3. Generate a structured recipe with these fields:
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
   python3 ~/.claude/commands/recipe-cli.py save \
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

5. Confirm to the user what was saved and show the recipe ID.

When the user runs `/recipe find <query>`:

```bash
python3 ~/.claude/commands/recipe-cli.py find "<query>"
```

Display the results in a readable format with intent, quality class, tags, and date.

When the user runs `/recipe list`:

```bash
python3 ~/.claude/commands/recipe-cli.py list
```

When the user runs `/recipe show <id>`:

```bash
python3 ~/.claude/commands/recipe-cli.py show "<id>"
```

Display the full recipe including the prompt template.

When the user runs `/recipe use <id>` (with optional `--var key=value`):

```bash
python3 ~/.claude/commands/recipe-cli.py use "<id>" [--var key=value ...]
```

Display the filled prompt template prominently. If there are unfilled `{{variables}}`, ask the user for values before proceeding. Once all variables are filled, ask: "Want me to run this recipe now?" If yes, execute the filled prompt as if the user had typed it.

When the user runs `/recipe stats`:

```bash
python3 ~/.claude/commands/recipe-cli.py stats
```

Display a clean summary of: total recipes, quality breakdown, top tags, total tracked cost.

When the user runs `/recipe-scan` (or `/recipe-scan N`):

```bash
python3 ~/.claude/commands/recipe-scan.py --days <N|all> --min-score 30 --limit 15
```

Display candidates ranked by score. For each high-scoring candidate, offer to extract a recipe using the `/recipe save` workflow.
