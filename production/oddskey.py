#!/usr/bin/env python3
"""
Resolve The Odds API key without it ever being typed into a transcript.

Lookup order:
  1. ODDS_API_KEY environment variable
  2. macOS Keychain  (service "the-odds-api", account "odds")   <- preferred
  3. ~/.nightly-line/.odds-key                                   <- fallback, chmod 600

Keychain is preferred because the key is never stored as plaintext on disk and
is protected by the login keychain. The file fallback exists for cron contexts
where the keychain may be locked.

    from oddskey import get_key
    key = get_key()          # raises RuntimeError with guidance if unset
"""
import os
import subprocess
from pathlib import Path

SERVICE = "the-odds-api"
ACCOUNT = "odds"
KEYFILE = Path.home() / ".nightly-line" / ".odds-key"


def _from_keychain():
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            k = r.stdout.strip()
            return k or None
    except Exception:
        pass
    return None


def _from_file():
    try:
        if KEYFILE.exists():
            k = KEYFILE.read_text().strip()
            return k or None
    except Exception:
        pass
    return None


def get_key(required=True):
    k = os.environ.get("ODDS_API_KEY") or _from_keychain() or _from_file()
    if k:
        return k
    if not required:
        return None
    raise RuntimeError(
        "No Odds API key found. Store it once with:\n"
        '  read -s -p "key: " K && security add-generic-password '
        f'-a {ACCOUNT} -s {SERVICE} -w "$K" && unset K\n'
        f"or write it to {KEYFILE} with chmod 600."
    )


def source():
    """Where the key came from — for diagnostics that must not print the key."""
    if os.environ.get("ODDS_API_KEY"):
        return "environment"
    if _from_keychain():
        return "keychain"
    if _from_file():
        return "file"
    return "not found"


if __name__ == "__main__":
    k = get_key(required=False)
    if not k:
        print("odds key: NOT FOUND")
        raise SystemExit(1)
    # never print the key itself
    print(f"odds key: found via {source()} · {len(k)} chars · ends …{k[-4:]}")
