#!/usr/bin/env bash
# poe-claude — launch Claude Code with Nino's voice stack appended to the system prompt.
#
# Usage:
#   poe-claude                    # interactive session with Poe loaded
#   poe-claude "vet this idea"    # one-shot prompt with Poe loaded
#   poe-claude --print "..."      # any normal claude flags still work
#
# Opt-in: regular `claude` stays clean. Only sessions started via this wrapper
# get Poe loaded.

set -euo pipefail

STACK="$HOME/.claude/poe/stack.md"
PREAMBLE_FILE="$(dirname "$0")/poe-preamble.md"

if [[ ! -f "$STACK" ]]; then
  echo "poe-claude: no stack found at $STACK" >&2
  echo "  run: python3 $(dirname "$0")/poe-extract.py run" >&2
  exit 1
fi

# Combine preamble + stack into a single system-prompt-file
TMP=$(mktemp -t poe-stack.XXXXXX.md)
trap 'rm -f "$TMP"' EXIT

if [[ -f "$PREAMBLE_FILE" ]]; then
  cat "$PREAMBLE_FILE" > "$TMP"
  echo "" >> "$TMP"
  echo "---" >> "$TMP"
  echo "" >> "$TMP"
fi
cat "$STACK" >> "$TMP"

exec claude --append-system-prompt-file "$TMP" "$@"
