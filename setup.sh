#!/usr/bin/env bash
# Install recipe CLI as Claude Code slash commands
# Run: bash tools/recipe-cli/setup.sh

set -e

COMMANDS_DIR="$HOME/.claude/commands"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$COMMANDS_DIR"

# Symlink so updates in the repo propagate automatically
ln -sf "$SCRIPT_DIR/recipe.md" "$COMMANDS_DIR/recipe.md"
ln -sf "$SCRIPT_DIR/recipe-cli.py" "$COMMANDS_DIR/recipe-cli.py"
ln -sf "$SCRIPT_DIR/recipe-scan.md" "$COMMANDS_DIR/recipe-scan.md"
ln -sf "$SCRIPT_DIR/recipe-scan.py" "$COMMANDS_DIR/recipe-scan.py"

echo "Recipe CLI installed. Commands available:"
echo "  /recipe save|find|list|show|use|stats"
echo "  /recipe-scan"
echo ""
echo "Database: ~/.claude/recall.db (auto-created on first use)"
