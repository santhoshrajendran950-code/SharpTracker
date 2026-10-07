#!/usr/bin/env python3
"""
Pseudonymise traders and blur figures for public posting.

The wallet identities are the whole edge. "Kch-Temp, 5.9% hold, $80,984 across
three markets" hands a reader the exact account to follow and a stake precise
enough to find on-chain in one search — which gives away the research for free
and moves the price of every future signal.

Two defences:

  ALIASES are deterministic, stable, and one-way. The same wallet always reads
  as the same codename, so a follower can track "Amber Falcon" across weeks, but
  the codename cannot be turned back into an address. A local salt makes the
  mapping unreproducible even by someone who knows the scheme and the wallet
  list — without the salt file they cannot confirm a guess.

  ROUNDING is deliberately coarse. $80,984 -> ~$80K, 5.87% -> ~6%. Precise
  figures are fingerprints: an exact stake plus an exact hold identifies one
  account out of a thousand. Rounded ones describe a shape instead.

The salt lives in .anon-salt (chmod 600) and is generated on first use. Deleting
it re-randomises every alias, so keep it if continuity matters.
"""
import hashlib, os, secrets

HERE = os.path.dirname(os.path.abspath(__file__))
SALT_FILE = os.path.join(HERE, ".anon-salt")

ADJ = ["Amber", "Slate", "Copper", "Indigo", "Umber", "Cobalt", "Sable", "Ivory",
       "Crimson", "Onyx", "Verdant", "Russet", "Cinder", "Marine", "Flint", "Ochre"]
NOUN = ["Falcon", "Heron", "Marlin", "Lynx", "Kestrel", "Otter", "Badger", "Osprey",
        "Wolfhound", "Sable", "Ibis", "Mako", "Harrier", "Stoat", "Gannet", "Caracal"]


def _salt():
    try:
        with open(SALT_FILE) as f:
            s = f.read().strip()
            if s:
                return s
    except FileNotFoundError:
        pass
    s = secrets.token_hex(16)
    fd = os.open(SALT_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(s)
    return s


def alias(who):
    """Stable, one-way codename for a wallet or username."""
    if not who:
        return "Unknown"
    h = hashlib.sha256((_salt() + str(who).strip().lower()).encode()).digest()
    return f"{ADJ[h[0] % len(ADJ)]} {NOUN[h[1] % len(NOUN)]}"


def money(n):
    """Coarse money. 80,984 -> ~$80K. 9,607 -> ~$10K. 1,736,575 -> ~$1.7M."""
    n = abs(n or 0)
    if n >= 1_000_000:
        return f"~${n/1e6:.1f}M"
    if n >= 100_000:
        return f"~${round(n/1e4)*10:,.0f}K"        # nearest 10K
    if n >= 10_000:
        return f"~${round(n/1e4)*10:,.0f}K"        # nearest 10K
    if n >= 1_000:
        return f"~${round(n/1e3):,.0f}K"           # nearest 1K
    return f"~${round(n, -2):,.0f}"


def pct(p):
    """Coarse percentage. 5.87 -> ~6%. 12.24 -> ~12%."""
    if p is None:
        return "—"
    return f"~{round(p):.0f}%"


def units(u):
    return f"{u:g}u" if u else "—"


def roi(p):
    """Nearest 5 points — a precise ROI on a small sample is false precision."""
    if p is None:
        return "—"
    return f"{round(p/5)*5:+.0f}%"
