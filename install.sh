#!/usr/bin/env bash
# Install claude-recipe-cli as global Claude Code slash commands.
# Usage: curl -fsSL https://raw.githubusercontent.com/nino-chavez/claude-recipe-cli/main/install.sh | bash
#   — or —
# git clone git@github.com:nino-chavez/claude-recipe-cli.git ~/.claude/recipe-cli && bash ~/.claude/recipe-cli/install.sh

set -e

INSTALL_DIR="${RECIPE_CLI_DIR:-$HOME/.claude/recipe-cli}"
COMMANDS_DIR="$HOME/.claude/commands"

# If running from curl pipe, clone first
if [ ! -f "$(dirname "$0")/recipe-cli.py" ] 2>/dev/null; then
    echo "Cloning claude-recipe-cli..."
    git clone --depth 1 https://github.com/nino-chavez/claude-recipe-cli.git "$INSTALL_DIR"
    SCRIPT_DIR="$INSTALL_DIR"
else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi

mkdir -p "$COMMANDS_DIR"

ln -sf "$SCRIPT_DIR/recipe.md" "$COMMANDS_DIR/recipe.md"
ln -sf "$SCRIPT_DIR/recipe-cli.py" "$COMMANDS_DIR/recipe-cli.py"
ln -sf "$SCRIPT_DIR/recipe-scan.md" "$COMMANDS_DIR/recipe-scan.md"
ln -sf "$SCRIPT_DIR/recipe-scan.py" "$COMMANDS_DIR/recipe-scan.py"

echo ""
echo "Recipe CLI installed! Commands available in any Claude Code session:"
echo "  /recipe save|find|list|show|use|stats"
echo "  /recipe-scan"
echo ""
echo "Database: ~/.claude/recall.db (auto-created on first use)"
echo "Update:   git -C $SCRIPT_DIR pull"
