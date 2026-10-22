#!/usr/bin/env python3
"""Emit MCP request headers for clients that support a headers helper.

Token sources, in order:
1. RECALL_CONTEXT_TOKEN in the environment (e.g. when wrapped by with-secret).
2. A cached copy in the macOS login Keychain, younger than the TTL.
3. A live 1Password read through with-secret, bounded by a timeout. A
   successful read refreshes the Keychain cache.
4. The expired Keychain copy, when 1Password does not answer in time.

Claude Code gives a headers helper about 10 seconds. A slow `op` call used to
exhaust that, and the client then fell back to OAuth and reported the server's
missing registration endpoint instead of the real cause. The cache keeps
session start off the 1Password path for most connections.

`--refresh` skips the fresh-cache step; `--clear-cache` deletes the cache.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time


ITEM = os.environ.get("RECALL_CONTEXT_1PASSWORD_ITEM", "Cloudflare recall-context-plane")
KEYCHAIN_SERVICE = os.environ.get("RECALL_CONTEXT_KEYCHAIN_SERVICE", "recall-context-plane-token")
KEYCHAIN_ACCOUNT = os.environ.get("USER", "recall")
TTL_SECONDS = int(os.environ.get("RECALL_CONTEXT_TOKEN_TTL", str(12 * 3600)))
OP_TIMEOUT_SECONDS = float(os.environ.get("RECALL_CONTEXT_OP_TIMEOUT", "7"))
SECURITY = "/usr/bin/security"
# Tokens are written to `security -i` on stdin, so they must not contain
# characters that its command parser would treat specially.
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]+$")


def log(message):
    print(f"mcp-auth-headers: {message}", file=sys.stderr)


def read_cache():
    """Return (token, fetched_at) from the Keychain, or (None, None)."""
    try:
        result = subprocess.run(
            [SECURITY, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode != 0:
        return None, None
    stamp, _, token = result.stdout.strip().partition(":")
    if not stamp.isdigit() or not TOKEN_PATTERN.match(token):
        return None, None
    return token, int(stamp)


def write_cache(token):
    if not TOKEN_PATTERN.match(token):
        log("token has unexpected characters; not caching")
        return
    value = f"{int(time.time())}:{token}"
    # Pass the value on stdin so it never appears in a process listing.
    command = (
        f'add-generic-password -U -s "{KEYCHAIN_SERVICE}" -a "{KEYCHAIN_ACCOUNT}" '
        f'-l "recall-context headers helper cache" -w "{value}"\n'
    )
    try:
        result = subprocess.run(
            [SECURITY, "-i"], input=command, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        log("could not write Keychain cache")
        return
    if result.returncode != 0:
        log("could not write Keychain cache")


def clear_cache():
    subprocess.run(
        [SECURITY, "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True, timeout=5, stdin=subprocess.DEVNULL,
    )


def find_with_secret():
    found = shutil.which("with-secret")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/with-secret")
    return fallback if os.access(fallback, os.X_OK) else None


def read_1password():
    with_secret = find_with_secret()
    if not with_secret:
        log("with-secret not found on PATH or in ~/.local/bin")
        return None
    try:
        result = subprocess.run(
            [with_secret, ITEM], capture_output=True, text=True,
            timeout=OP_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log(f"1Password did not answer within {OP_TIMEOUT_SECONDS:g}s")
        return None
    except OSError as error:
        log(f"could not run with-secret: {error.strerror}")
        return None
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        log("1Password read failed")
        return None
    return token


def resolve_token(refresh=False):
    token = os.environ.get("RECALL_CONTEXT_TOKEN", "").strip()
    if token:
        return token

    cached, fetched_at = read_cache()
    if cached and not refresh and time.time() - fetched_at < TTL_SECONDS:
        return cached

    token = read_1password()
    if token:
        write_cache(token)
        return token

    if cached:
        log("using expired Keychain copy")
        return cached
    return None


def main(argv):
    if "--clear-cache" in argv:
        clear_cache()
        return 0
    token = resolve_token(refresh="--refresh" in argv)
    if not token:
        log("no token available")
        return 1
    print(json.dumps({"Authorization": f"Bearer {token}"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
