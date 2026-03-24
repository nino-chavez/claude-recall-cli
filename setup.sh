#!/usr/bin/env bash
# Install recall CLI as Claude Code slash commands
# Run: bash setup.sh

set -e

COMMANDS_DIR="$HOME/.claude/commands"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$COMMANDS_DIR"

# Symlink so updates in the repo propagate automatically
ln -sf "$SCRIPT_DIR/recall.md" "$COMMANDS_DIR/recall.md"
ln -sf "$SCRIPT_DIR/recall-cli.py" "$COMMANDS_DIR/recall-cli.py"
ln -sf "$SCRIPT_DIR/recall-scan.md" "$COMMANDS_DIR/recall-scan.md"
ln -sf "$SCRIPT_DIR/recall-scan.py" "$COMMANDS_DIR/recall-scan.py"

echo "Recall CLI installed. Commands available:"
echo "  /recall save|find|list|show|use|stats"
echo "  /recall-scan"
echo ""
echo "Database: ~/.claude/recall.db (auto-created on first use)"
