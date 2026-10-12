#!/usr/bin/env python3
"""
Novig prices straight from the exchange (docs.novig.com), plus its public
daily trade/market CSVs.

Every private call is signed NOVIG-V3 (Ed25519) with openssl — no pip
packages, in keeping with the rest of ~/.nightly-line. The signature covers:

    NOVIG-V3 \\n <unix ms> \\n <METHOD> \\n <path> \\n <canonical query> \\n <sha256 hex of body>

Headers: Novig-Key-Id, Novig-Timestamp, Novig-Signature.  Clock skew ±30 s.

Hosts:   NOVIG_HOST  (default https://api.novig.com; paper = https://api.paper.novig.com)
Key id:  $NOVIG_KEY_ID   or keychain -s novig -a key-id
PEM:     $NOVIG_PEM      or ~/.nightly-line/.novig-read.pem   (chmod 600)

The key used here should be a `trading::read` key — it can read the catalog
and order books but can never place an order or move money. `python3 novig.py
setup` creates one from the management key (see docs/exchange-apis.md).

Price semantics: a market has two outcomes, each with its own ladder of
resting bids. A bid at 0.665 on outcome A is liquidity for outcome B at
1 - 0.665 = 0.335. So the price WE pay for side S is 1 - best_bid(opposite).
That probability is converted to American odds to match the other books.
Novig charges a fee on net winnings (market.fee.coefficient); the raw price is
returned like every other book's, so compare apples to apples.

Public surface used by books.py:
    prices_for(h) -> [("NOVIG", american_odds, available_qty)]  or []
    raises exkeys.NotConfigured when no key is present.

    python3 novig.py                  smoke test: echo + leagues + 3 NFL markets
    python3 novig.py NFL              list live markets for a league
    python3 novig.py setup MGMT_ID MGMT.pem [label]   create a read-only key
    python3 novig.py public 2026-10-11                 download that day's CSVs
"""
import base64, hashlib, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import unquote_to_bytes

sys.path.insert(0, str(Path(__file__).parent))
from exkeys import get, describe, NotConfigured  # noqa: E402

LABEL = "NOVIG"
HOST = os.environ.get("NOVIG_HOST", "https://api.novig.com")
PEM_DEFAULT = Path.home() / ".nightly-line" / ".novig-read.pem"
PUBLIC = "https://data.novig.com/reporting/trade-data"
OPENSSL = next((p for p in ("/opt/homebrew/bin/openssl", "/usr/local/bin/openssl",
                            "/usr/bin/openssl") if Path(p).exists()), "openssl")

# board league code -> Novig league string (validated against /v3/types/leagues)
LEAGUE = {"NFL": "NFL", "NCAAF": "NCAAF", "MLB": "MLB", "NBA": "NBA",
          "WNBA": "WNBA", "NCAAB": "NCAAB", "NHL": "NHL"}
MARKET_TYPE = {"h2h": "MONEY", "spreads": "SPREAD", "totals": "TOTAL"}

_cache, _types = {}, {}


# ---------------------------------------------------------------- credentials
def _key():
    kid = get("novig", "key-id", env="NOVIG_KEY_ID")
    pem = os.environ.get("NOVIG_PEM") or str(PEM_DEFAULT)
    if not kid or not Path(pem).exists():
        raise NotConfigured(
            "Novig: no key. Need a key id (security add-generic-password -U -a key-id "
            f"-s novig -w <id>) and a PEM at {pem}. See docs/exchange-apis.md.")
    return kid, pem


def status():
    kid = describe("novig", "key-id", env="NOVIG_KEY_ID")
    pem = os.environ.get("NOVIG_PEM") or str(PEM_DEFAULT)
    return f"novig: host={HOST} key-id={kid} pem={'present' if Path(pem).exists() else 'missing'}"


# ------------------------------------------------------------------- signing
_UNRESERVED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _enc(raw):
    return "".join(chr(b) if b in _UNRESERVED else f"%{b:02X}" for b in raw)


def canonical_query(raw):
    """Spec §'The canonical query': split, decode %XX only, re-encode, sort."""
    pairs = []
    for part in (raw or "").split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        pairs.append((_enc(unquote_to_bytes(k)), _enc(unquote_to_bytes(v))))
    pairs.sort()
    return "&".join(f"{k}={v}" for k, v in pairs)


def string_to_sign(ts, method, path, query, body):
    return "\n".join(["NOVIG-V3", str(ts), method.upper(), path,
                      canonical_query(query), hashlib.sha256(body).hexdigest()])


def sign(pem, text):
    """Ed25519 signature of `text` via openssl, standard padded base64."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(text.encode())
        msg = f.name
    try:
        p = subprocess.run([OPENSSL, "pkeyutl", "-sign", "-inkey", pem, "-rawin",
                            "-in", msg], capture_output=True)
    finally:
        os.unlink(msg)
    if p.returncode != 0:
        raise RuntimeError(f"Novig: openssl sign failed: {p.stderr.decode()[:200]}")
    return base64.b64encode(p.stdout).decode()


def call(method, path, query="", body=None, key=None, raw=False):
    """Signed request. Returns parsed JSON (or (status, text) when raw)."""
    kid, pem = key or _key()
    data = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
    ts = str(int(time.time() * 1000))
    sig = sign(pem, string_to_sign(ts, method, path, query, data))
    url = HOST + path + (f"?{query}" if query else "")
    cmd = ["curl", "-s", "--max-time", "30", "-X", method.upper(), "-w", "\n%{http_code}",
           "-H", f"Novig-Key-Id: {kid}", "-H", f"Novig-Timestamp: {ts}",
           "-H", f"Novig-Signature: {sig}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    cmd.append(url)
    p = subprocess.run(cmd, input=data, capture_output=True)
    out, _, code = p.stdout.decode(errors="replace").rpartition("\n")
    try:
        code = int(code)
    except ValueError:
        raise RuntimeError(f"Novig: no HTTP response from {path}")
    if raw:
        return code, out
    if code == 429:
        raise RuntimeError(f"Novig: 429 rate-limited on {path} — honour Retry-After")
    if code == 451:
        raise RuntimeError("Novig: 451 location check failed (VPN/proxy or restricted state)")
    if code == 401:
        raise RuntimeError(f"Novig: 401 — host/key/clock/signature wrong: {out[:160]}")
    if code >= 400:
        raise RuntimeError(f"Novig: HTTP {code} on {path}: {out[:200]}")
    try:
        return json.loads(out) if out else {}
    except ValueError:
        raise RuntimeError(f"Novig: non-JSON body from {path}")


def _get(path, query=""):
    ck = (path, canonical_query(query))
    if ck not in _cache:
        _cache[ck] = call("GET", path, query)
    return _cache[ck]


# ------------------------------------------------------------------- queries
def _flatten(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _flatten(v)
    elif isinstance(x, list):
        for v in x:
            yield from _flatten(v)


def types(kind):
    """Strings Novig accepts for `league` / `markets` filters."""
    if kind not in _types:
        d = _get(f"/v3/types/{kind}")
        _types[kind] = sorted(set(_flatten(d.get("items", d))))
    return _types[kind]


def league_for(code):
    want = LEAGUE.get(code, code)
    for s in types("leagues"):
        if s.upper() == want.upper():
            return s
    return None


def market_type_for(kind):
    want = MARKET_TYPE[kind]
    for s in types("markets"):
        if s.upper() == want:
            return s
    for s in types("markets"):
        if want in s.upper():
            return s
    return want


def catalog_markets(league, start=None, window_h=3, market_type=None, limit=5000):
    q = [f"league={league}", f"limit={limit}"]
    if start is not None:
        lo = int((start - timedelta(hours=window_h)).timestamp() * 1000)
        hi = int((start + timedelta(hours=window_h)).timestamp() * 1000)
        q += [f"startsAfter={lo}", f"startsBefore={hi}"]
    if market_type:
        q.append(f"marketType={market_type}")
    items = []
    after = None
    while True:
        qq = "&".join(q + ([f"after={after}"] if after else []))
        d = _get("/v3/catalog/markets", qq)
        items += d.get("items") or []
        after = d.get("next")
        if not after or len(items) >= limit:
            break
    return items


def book(market_id):
    return _get(f"/v3/catalog/markets/{market_id}/book")


def american(prob):
    """Cost probability (0,1) -> American odds, as the books quote them."""
    if prob >= 0.5:
        return -int(round(100 * prob / (1 - prob)))
    return int(round(100 * (1 - prob) / prob))


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


def _home_of(desc):
    """'Chiefs at Bills — Moneyline' -> 'Bills'; None if the form is unknown."""
    m = re.match(r"(.+?)\s+(?:at|@)\s+(.+?)(?:\s+[—-]\s+.*)?$", desc or "")
    return m.group(2).strip() if m else None


def _best_price_for(bk, outcome_id, other_ids):
    """What a taker pays for `outcome_id`: 1 - best resting bid on the other side."""
    orders = (bk.get("book") or bk).get("orders") or {}
    best, qty = None, 0
    for oid in other_ids:
        ladder = orders.get(oid) or []
        if ladder:
            top = ladder[0]                     # arrays are best-first by spec
            p = float(top["price"])
            if best is None or p > best:
                best, qty = p, top.get("qty", 0)
    if best is None:
        return None, 0
    return 1 - best, qty


def prices_for(h):
    """[(label, american_odds, available_qty)] for the row's side, or []."""
    league = league_for(h.get("league") or "")
    if not league:
        return []
    kind = _market_kind(h)
    mtype = market_type_for(kind)
    line = _line(h) if kind != "h2h" else None
    parts = re.split(r"\s+vs\.?\s+", h.get("game") or "", maxsplit=1)
    want_a, want_b = parts[0], (parts[1] if len(parts) > 1 else "")
    for m in catalog_markets(league, h.get("start"), market_type=mtype):
        if (m.get("status") or "OPEN").upper() not in ("OPEN", "ACTIVE"):
            continue
        desc = m.get("description") or ""
        names = [o.get("name") or "" for o in m.get("outcomes") or []]
        hay = " ".join([desc] + names)
        if not (_team_match(want_a, hay) and _team_match(want_b, hay)):
            continue
        if line is not None:
            st = m.get("strike")
            if st is None:
                continue
            st = float(st)
            if kind == "totals":
                if abs(st - abs(line)) > 0.01:
                    continue
            else:                                # strike = HOME side's handicap
                home = _home_of(desc)
                mine = line if (home and _team_match(h["side"], home)) else -line
                if abs(st - mine) > 0.01:
                    continue
        mine_id, others = None, []
        for o in m.get("outcomes") or []:
            nm = o.get("name") or ""
            ok = (nm.lower() == h["side"].lower()) if kind == "totals" \
                else _team_match(h["side"], nm)
            if ok and mine_id is None:
                mine_id = o["outcomeId"]
            else:
                others.append(o["outcomeId"])
        if mine_id is None or not others:
            continue
        prob, qty = _best_price_for(book(m["marketId"]), mine_id, others)
        if prob is None or not 0 < prob < 1:
            return []                            # listed but no resting liquidity
        return [(LABEL, american(prob), qty)]
    return []


# -------------------------------------------------------------- public data
def public_dates():
    p = subprocess.run(["curl", "-s", "--max-time", "30", f"{PUBLIC}/index.json"],
                       capture_output=True, text=True)
    d = json.loads(p.stdout or "{}")
    return d.get("dates", []), d.get("marketDates", [])


def public_csv(date, which, dest_dir):
    """Download <date>/trades.csv or markets.csv into dest_dir; returns the path."""
    assert which in ("trades", "markets")
    dest = Path(dest_dir) / f"novig-{which}-{date}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["curl", "-s", "--max-time", "120", "-f", "-o", str(dest),
                        f"{PUBLIC}/{date}/{which}.csv"])
    if p.returncode != 0:
        raise RuntimeError(f"Novig public data: {which}.csv for {date} not available")
    return dest


# ------------------------------------------------------------------- setup
def setup(mgmt_id, mgmt_pem, label="sharptracker"):
    """One-time: open a subaccount and give it a trading::read key for this Mac.

    The management key is used ONLY here. The trading key created alongside the
    subaccount is generated to a temp file and destroyed — we never want a key
    on this machine that can place orders.
    """
    mgmt = (mgmt_id, mgmt_pem)
    echo = call("POST", "/v3/echo", body={"ping": "sharptracker"}, key=mgmt)
    if echo.get("ping") != "sharptracker":
        raise RuntimeError(f"echo failed: {echo}")
    print("echo ok — management key, clock and signer are all good")

    tmp = Path(tempfile.mkdtemp())
    trade_pem, trade_pub = tmp / "trade.pem", tmp / "trade.pub.pem"
    subprocess.run([OPENSSL, "genpkey", "-algorithm", "ed25519", "-out", str(trade_pem)], check=True)
    subprocess.run([OPENSSL, "pkey", "-in", str(trade_pem), "-pubout", "-out", str(trade_pub)], check=True)
    opened = call("POST", "/v3/account/subaccounts", key=mgmt, body={
        "label": label, "publicKey": trade_pub.read_text(), "algorithm": "Ed25519"})
    sub = opened.get("keyId")
    if not sub:
        raise RuntimeError(f"subaccount open failed: {opened}")
    print(f"subaccount '{label}' opened — id {sub}")
    for f in (trade_pem, trade_pub):
        f.unlink()
    tmp.rmdir()
    print("trading key material destroyed (this Mac keeps read-only access only)")

    PEM_DEFAULT.parent.mkdir(parents=True, exist_ok=True)
    pub = PEM_DEFAULT.with_suffix(".pub.pem")
    subprocess.run([OPENSSL, "genpkey", "-algorithm", "ed25519", "-out", str(PEM_DEFAULT)], check=True)
    PEM_DEFAULT.chmod(0o600)
    subprocess.run([OPENSSL, "pkey", "-in", str(PEM_DEFAULT), "-pubout", "-out", str(pub)], check=True)
    created = call("POST", f"/v3/account/subaccounts/{sub}/keys", key=mgmt, body={
        "name": f"{label}-read", "publicKey": pub.read_text(),
        "algorithm": "Ed25519", "scope": "trading::read"})
    kid = created.get("keyId")
    if not kid:
        raise RuntimeError(f"read key creation failed: {created}")
    subprocess.run(["security", "add-generic-password", "-U", "-a", "key-id", "-s", "novig",
                    "-w", kid], check=True)
    print(f"read key created and stored: keychain novig/key-id, PEM {PEM_DEFAULT}")
    print("now run:  python3 novig.py   (smoke test with the read key)")


# --------------------------------------------------------------------- cli
if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["setup"]:
        if len(args) < 3:
            print("usage: novig.py setup <MGMT_KEY_ID> <mgmt.pem> [label]")
            sys.exit(1)
        setup(args[1], args[2], *(args[3:4]))
        sys.exit(0)
    if args[:1] == ["public"]:
        date = args[1] if len(args) > 1 else None
        dates, mdates = public_dates()
        print(f"public data: {len(dates)} trade days, {len(mdates)} market days; latest {dates[-1:] or '-'}")
        if date:
            for w in ("trades", "markets"):
                try:
                    print(" ", public_csv(date, w, Path.home() / ".nightly-line" / "novig-public"))
                except RuntimeError as e:
                    print(" ", e)
        sys.exit(0)
    print(status())
    try:
        _key()
    except NotConfigured as e:
        print(e)
        sys.exit(2)
    code, out = call("POST", "/v3/echo", body={"ping": 1}, raw=True)
    print(f"echo -> {code} {out[:80]}")
    if code != 200:
        sys.exit(1)
    print("leagues:", ", ".join(types("leagues")[:30]))
    print("market types:", ", ".join(types("markets")[:30]))
    league = args[0] if args else "NFL"
    ms = catalog_markets(league_for(league) or league, limit=200)
    print(f"{league}: {len(ms)} open markets")
    for m in ms[:3]:
        bk = book(m["marketId"])
        orders = (bk.get("book") or bk).get("orders") or {}
        depth = {o.get("name"): len(orders.get(o["outcomeId"]) or []) for o in m.get("outcomes") or []}
        print(f"  {m.get('marketType'):>6} {m.get('strike') or '':>6} {m.get('description')}  depth={depth}")
