#!/usr/bin/env bash
# Install claude-recall-cli as global Claude Code slash commands.
# Usage: curl -fsSL https://raw.githubusercontent.com/nino-chavez/claude-recall-cli/main/install.sh | bash
#   — or —
# git clone git@github.com:nino-chavez/claude-recall-cli.git ~/.claude/recall-cli && bash ~/.claude/recall-cli/install.sh

set -e

INSTALL_DIR="${RECALL_CLI_DIR:-$HOME/.claude/recall-cli}"
COMMANDS_DIR="$HOME/.claude/commands"

# If running from curl pipe, clone first
if [ ! -f "$(dirname "$0")/recall-cli.py" ] 2>/dev/null; then
    echo "Cloning claude-recall-cli..."
    git clone --depth 1 https://github.com/nino-chavez/claude-recall-cli.git "$INSTALL_DIR"
    SCRIPT_DIR="$INSTALL_DIR"
else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi

mkdir -p "$COMMANDS_DIR"

ln -sf "$SCRIPT_DIR/recall.md" "$COMMANDS_DIR/recall.md"
ln -sf "$SCRIPT_DIR/recall-cli.py" "$COMMANDS_DIR/recall-cli.py"
ln -sf "$SCRIPT_DIR/recall-scan.md" "$COMMANDS_DIR/recall-scan.md"
ln -sf "$SCRIPT_DIR/recall-scan.py" "$COMMANDS_DIR/recall-scan.py"

echo ""
echo "Recall CLI installed! Commands available in any Claude Code session:"
echo "  /recall save|find|list|show|use|stats"
echo "  /recall-scan"
echo ""
echo "Database: ~/.claude/recall.db (auto-created on first use)"
echo "Update:   git -C $SCRIPT_DIR pull"
