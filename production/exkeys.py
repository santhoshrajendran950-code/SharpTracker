#!/usr/bin/env python3
"""
Credential lookup for the exchange clients (prophetx.py, novig.py).

Same contract as oddskey.py, generalised: environment variable first, then the
macOS login Keychain, then a chmod-600 file under ~/.nightly-line. Nothing here
ever prints a secret; `describe()` reports only where a value came from.

    from exkeys import get
    key = get("prophetx", "affiliate", env="PROPHETX_API_KEY")

Store a value once (never paste it into a transcript):

    read -s -p "value: " V && security add-generic-password -U \\
        -a <account> -s <service> -w "$V" && unset V
"""
import os
import subprocess
from pathlib import Path

HOME = Path.home() / ".nightly-line"


class NotConfigured(RuntimeError):
    """Raised when a client has no credentials. Callers treat it as 'skip'."""


def _keychain(service, account):
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip() or None
    except Exception:
        pass
    return None


def _file(name):
    try:
        p = HOME / name
        if p.exists():
            return p.read_text().strip() or None
    except Exception:
        pass
    return None


def get(service, account, env=None, file=None):
    """First hit wins: $env, keychain(service, account), ~/.nightly-line/<file>."""
    if env and os.environ.get(env):
        return os.environ[env]
    v = _keychain(service, account)
    if v:
        return v
    if file:
        return _file(file)
    return None


def describe(service, account, env=None, file=None):
    if env and os.environ.get(env):
        return "environment"
    if _keychain(service, account):
        return "keychain"
    if file and _file(file):
        return "file"
    return "not found"
