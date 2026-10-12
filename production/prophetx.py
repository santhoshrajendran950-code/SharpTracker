#!/usr/bin/env python3
"""
ProphetX prices straight from the exchange (docs.prophetx.co).

Two ways in, picked automatically from whichever credential is present:

  Market Data API  (read-only, simplest)      header  Authorization: <affiliate key>
      paths  /affiliate/get_tournaments, /affiliate/get_sport_events,
             /v4/affiliate/get_markets, /v4/affiliate/get_multiple_markets
  Trading API      (access key + secret key)  POST /auth/login -> Bearer token
      paths  /mm/get_tournaments, /mm/get_sport_events, /v4/mm/get_markets ...

Environments:  PROPHETX_ENV=production (default) | sandbox
  production  https://cash.api.prophetx.co/partner
  sandbox     https://api.sandbox.prophetx.dev/partner

Credentials (see docs/exchange-apis.md for how to obtain them):
  affiliate key     $PROPHETX_API_KEY     or keychain  -s prophetx -a affiliate
  access key        $PROPHETX_ACCESS_KEY  or keychain  -s prophetx -a access
  secret key        $PROPHETX_SECRET_KEY  or keychain  -s prophetx -a secret

The trading-API session token (20 min) is cached in ~/.nightly-line/.prophetx-session
because ProphetX counts live sessions PER KEY — logging in from every 15-minute
job would exhaust the session limit.

Public surface used by books.py:
    prices_for(h) -> [("PX", american_odds, available_quantity)]  or []
    raises exkeys.NotConfigured when no credential is present.

    python3 prophetx.py            smoke test: tournaments + one NFL market
    python3 prophetx.py NFL        list the live events ProphetX has for a league
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from exkeys import get, describe, NotConfigured  # noqa: E402

LABEL = "PX"
BASES = {
    "production": "https://cash.api.prophetx.co/partner",
    "sandbox": "https://api.sandbox.prophetx.dev/partner",
}
ENV = os.environ.get("PROPHETX_ENV", "production")
BASE = BASES.get(ENV, BASES["production"])
SESSION_FILE = Path.home() / ".nightly-line" / ".prophetx-session"

# board league -> substrings that identify the tournament name on ProphetX
TOURNAMENT_HINTS = {
    "NFL": ["nfl"],
    "NCAAF": ["ncaa football", "college football", "ncaaf"],
    "MLB": ["mlb"],
    "NBA": ["nba"],
    "WNBA": ["wnba"],
    "NCAAB": ["ncaa basketball", "college basketball", "ncaab"],
    "NHL": ["nhl"],
    "ATP": ["atp"],
    "WTA": ["wta"],
}
MARKET_TYPE = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}

_cache = {}


# ---------------------------------------------------------------- credentials
def _creds():
    aff = get("prophetx", "affiliate", env="PROPHETX_API_KEY")
    if aff:
        return ("affiliate", aff)
    ak = get("prophetx", "access", env="PROPHETX_ACCESS_KEY")
    sk = get("prophetx", "secret", env="PROPHETX_SECRET_KEY")
    if ak and sk:
        return ("trading", (ak, sk))
    raise NotConfigured(
        "ProphetX: no credential. Store an affiliate key "
        "(security add-generic-password -U -a affiliate -s prophetx -w ...) "
        "or an access/secret pair (-a access / -a secret). See docs/exchange-apis.md.")


def status():
    """Diagnostic line, never includes a secret."""
    aff = describe("prophetx", "affiliate", env="PROPHETX_API_KEY")
    acc = describe("prophetx", "access", env="PROPHETX_ACCESS_KEY")
    return f"prophetx: env={ENV} affiliate={aff} access/secret={acc}"


# ----------------------------------------------------------------------- http
def _http(method, url, headers=None, body=None):
    cmd = ["curl", "-s", "--max-time", "30", "-X", method, "-w", "\n%{http_code}",
           "-H", "Accept: application/json"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data", json.dumps(body)]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True)
    out, _, code = p.stdout.rpartition("\n")
    try:
        code = int(code)
    except ValueError:
        raise RuntimeError(f"ProphetX: no HTTP response from {url.split('?')[0]}")
    if code == 429:
        raise RuntimeError(f"ProphetX: 429 rate-limited on {url.split('?')[0]} — back off")
    if code == 401 or code == 403:
        raise RuntimeError(f"ProphetX: HTTP {code} (credential rejected or session expired)")
    if code >= 400:
        raise RuntimeError(f"ProphetX: HTTP {code} on {url.split('?')[0]}: {out[:200]}")
    try:
        return json.loads(out) if out else {}
    except ValueError:
        raise RuntimeError(f"ProphetX: non-JSON body from {url.split('?')[0]}")


def _token(ak, sk):
    """Trading-API bearer token, reused across processes until it nears expiry."""
    try:
        s = json.loads(SESSION_FILE.read_text())
        if s.get("env") == ENV and s.get("exp", 0) - time.time() > 60:
            return s["token"]
    except Exception:
        pass
    d = _http("POST", f"{BASE}/auth/login", body={"access_key": ak, "secret_key": sk})
    tok = (d.get("data") or {}).get("access_token")
    if not tok:
        raise RuntimeError("ProphetX: login returned no access_token")
    try:
        SESSION_FILE.write_text(json.dumps({"env": ENV, "token": tok,
                                            "exp": time.time() + 19 * 60}))
        SESSION_FILE.chmod(0o600)
    except Exception:
        pass
    return tok


def _get(path, **params):
    """GET relative to BASE with the right auth + path family for the credential."""
    mode, cred = _creds()
    fam = "affiliate" if mode == "affiliate" else "mm"
    path = path.replace("{fam}", fam)
    if mode == "affiliate":
        headers = {"Authorization": cred}
    else:
        headers = {"Authorization": f"Bearer {_token(*cred)}"}
    q = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            q += [f"{k}={x}" for x in v]          # event_ids=1&event_ids=2
        elif v is not None:
            q.append(f"{k}={v}")
    url = f"{BASE}/{path}" + (f"?{'&'.join(q)}" if q else "")
    ck = url
    if ck not in _cache:
        _cache[ck] = _http("GET", url, headers=headers)
    return _cache[ck]


# ------------------------------------------------------------------- queries
def tournaments(active_only=True):
    d = _get("{fam}/get_tournaments", has_active_events="true" if active_only else None)
    return (d.get("data") or {}).get("tournaments") or []


def tournaments_for(league):
    hints = TOURNAMENT_HINTS.get(league) or [league.lower()]
    out = []
    for t in tournaments():
        nm = (t.get("name") or "").lower()
        if any(h in nm for h in hints):
            out.append(t)
    return out


def events(tournament_id):
    d = _get("{fam}/get_sport_events", tournament_id=tournament_id)
    return (d.get("data") or {}).get("sport_events") or []


def markets(event_id, types=None):
    d = _get("v4/{fam}/get_markets", event_id=event_id,
             market_types=",".join(types) if types else None)
    data = d.get("data")
    if isinstance(data, dict):           # defensive: keyed-by-event shape
        data = data.get(str(event_id)) or data.get(event_id) or []
    return data or []


# ------------------------------------------------------------------ matching
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _team_match(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a in b or b in a)


def _market_kind(h):
    mkt, side = h.get("market") or "", h.get("side") or ""
    if side in ("Over", "Under") or re.search(r"O/U|Total", mkt, re.I):
        return "totals"
    if re.match(r"(?:1H\s+)?Spread:", mkt):
        return "spreads"
    return "h2h"


def _line(h):
    m = re.search(r"(-?\d+\.?\d*)\s*$", h.get("market") or "")
    return float(m.group(1)) if m else None


def _parse_when(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def find_event(h):
    """The ProphetX event matching a board row (same team pair, start within 3h)."""
    league = h.get("league") or ""
    if league in TOURNAMENT_HINTS:
        cands = tournaments_for(league)
    elif league in ("TENNIS", "ATP", "WTA"):
        cands = tournaments_for("ATP") + tournaments_for("WTA")
    else:
        return None                              # soccer etc.: not mapped yet
    parts = re.split(r"\s+vs\.?\s+", h.get("game") or "", maxsplit=1)
    want_a, want_b = parts[0], (parts[1] if len(parts) > 1 else "")
    start = h.get("start")
    for t in cands:
        for ev in events(t["id"]):
            when = _parse_when(ev.get("scheduled") or "")
            if start is not None and when is not None and \
                    abs((when - start).total_seconds()) > 3 * 3600:
                continue
            names = [c.get("name") or "" for c in ev.get("competitors") or []]
            if not names:
                names = re.split(r"\s+(?:vs\.?|at|@)\s+", ev.get("name") or "")
            if any(_team_match(want_a, n) for n in names) and \
                    any(_team_match(want_b, n) for n in names):
                return ev
    return None


def prices_for(h):
    """[(label, american_odds, quantity)] for the row's side, best price first."""
    ev = find_event(h)
    if not ev:
        return []
    kind = _market_kind(h)
    want_type = MARKET_TYPE[kind]
    line = _line(h) if kind != "h2h" else None
    out = []
    for m in markets(ev["event_id"], types=[want_type]):
        if (m.get("type") or "").lower() != want_type:
            continue
        sels = m.get("selections") or []
        flat = [s for grp in sels for s in (grp if isinstance(grp, list) else [grp])]
        for s in flat:
            nm = s.get("name") or ""
            if kind == "totals":
                if nm.lower() != h["side"].lower():
                    continue
            elif not _team_match(h["side"], nm):
                continue
            if line is not None:
                st = s.get("strike")
                if st is None:
                    st = m.get("strike")
                if st is None or abs(abs(float(st)) - abs(line)) > 0.01:
                    continue
            px = s.get("price")
            if px is None:
                continue
            out.append((LABEL, int(px), s.get("quantity")))
    out.sort(key=lambda t: -t[1])
    return out[:1]


# --------------------------------------------------------------------- cli
if __name__ == "__main__":
    print(status())
    try:
        _creds()
    except NotConfigured as e:
        print(e)
        sys.exit(2)
    league = sys.argv[1] if len(sys.argv) > 1 else None
    ts = tournaments()
    print(f"ok — {len(ts)} active tournaments on {ENV}")
    if league:
        for t in tournaments_for(league):
            evs = events(t["id"])
            print(f"  {t['name']}: {len(evs)} events")
            for ev in evs[:12]:
                print(f"    {ev.get('event_id')}  {ev.get('scheduled')}  {ev.get('name')}")
    else:
        for t in ts[:15]:
            print(f"  {t['id']:>6}  {t['name']}")
        nfl = tournaments_for("NFL")
        if nfl:
            evs = events(nfl[0]["id"])
            if evs:
                ms = markets(evs[0]["event_id"], ["moneyline"])
                print(f"sample: {evs[0].get('name')} -> {json.dumps(ms[:1])[:400]}")
