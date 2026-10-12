#!/usr/bin/env python3
"""
Novig prices straight from the exchange (docs.novig.com), plus its public
daily trade/market CSVs.

Every private call is signed NOVIG-V3 (Ed25519) with openssl — no pip
packages, in keeping with the rest of ~/.nightly-line. The signature covers:

    NOVIG-V3 \\n <unix ms> \\n <METHOD> \\n <path> \\n <canonical query> \\n <sha256 hex of body>

Headers: Novig-Key-Id, Novig-Timestamp, Novig-Signature.  Clock skew ±30 s.

Hosts:   NOVIG_HOST  (default https://api.novig.com; paper = https://api.paper.novig.com)
Keys are per environment ('-paper' suffix on Paper):
  read   keychain novig/key-id[-paper]        ~/.nightly-line/.novig-read[-paper].pem
  trade  keychain novig/trade-key-id[-paper]  ~/.nightly-line/.novig-trade[-paper].pem
  mgmt   keychain novig/mgmt-key-id[-paper]   ~/.nightly-line/.novig-mgmt[-paper].pem
($NOVIG_KEY_ID / $NOVIG_PEM override the read key.)

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
    python3 novig.py setup MGMT_ID MGMT.pem [label] [--trading] [--fund N]
    python3 novig.py fund 100 | balance               (management key)
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
HOME = Path.home() / ".nightly-line"
PUBLIC = "https://data.novig.com/reporting/trade-data"
OPENSSL = next((p for p in ("/opt/homebrew/bin/openssl", "/usr/local/bin/openssl",
                            "/usr/bin/openssl") if Path(p).exists()), "openssl")

# board league code -> Novig league string (validated against /v3/types/leagues)
LEAGUE = {"NFL": "NFL", "NCAAF": "NCAAF", "MLB": "MLB", "NBA": "NBA",
          "WNBA": "WNBA", "NCAAB": "NCAAB", "NHL": "NHL"}
MARKET_TYPE = {"h2h": "MONEY", "spreads": "SPREAD", "totals": "TOTAL"}

_cache, _types = {}, {}


# ---------------------------------------------------------------- credentials
def _sfx():
    """Keys are per environment: '-paper' names the play-money set."""
    return "-paper" if "paper" in HOST else ""


KEY_FILES = {   # scope -> (keychain account, PEM file)   (+ env suffix)
    "read":  ("key-id",       ".novig-read{sfx}.pem"),
    "trade": ("trade-key-id", ".novig-trade{sfx}.pem"),
    "mgmt":  ("mgmt-key-id",  ".novig-mgmt{sfx}.pem"),
}


def _key(scope="read"):
    acct, pem = KEY_FILES[scope]
    sfx = _sfx()
    if scope == "read":
        kid = get("novig", acct + sfx, env="NOVIG_KEY_ID")
        pemp = os.environ.get("NOVIG_PEM") or str(HOME / pem.format(sfx=sfx))
    else:
        kid = get("novig", acct + sfx)
        pemp = str(HOME / pem.format(sfx=sfx))
    if not kid or not Path(pemp).exists():
        raise NotConfigured(
            f"Novig: no {scope} key for {HOST}. Need keychain novig/{acct}{sfx} and {pemp}. "
            "See docs/exchange-apis.md.")
    return kid, pemp


def status():
    out = [f"novig: host={HOST}"]
    for scope in ("read", "trade", "mgmt"):
        try:
            _key(scope)
            out.append(f"{scope}=present")
        except NotConfigured:
            out.append(f"{scope}=missing")
    return " ".join(out)


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
        if isinstance(d, dict):
            d = d.get("items", d)
        _types[kind] = sorted(set(_flatten(d)))
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


def snap_down(prob):
    """Model probability -> the nearest allowed grid price at or below it, as the
    3-place string the API wants. Grid: 0.001 steps in (0,0.05] and [0.95,1),
    0.005 steps between. None if off the grid entirely."""
    milli = int(round(prob * 1000))
    if 1 <= milli <= 50 or 950 <= milli <= 999:
        pass
    elif 51 <= milli <= 949:
        milli -= milli % 5
    else:
        return None
    return f"{milli / 1000:.3f}"


def prob_of(american_odds):
    """American odds -> implied probability (cost per $1 contract)."""
    a = float(american_odds)
    return (-a) / (-a + 100) if a < 0 else 100 / (a + 100)


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


def _abbr_match(abbr, fullname):
    """'BUF' ~ 'Buffalo Bills', 'LAR' ~ 'Los Angeles Rams', 'NYJ' ~ 'New York Jets'."""
    a = re.sub(r"[^A-Z]", "", (abbr or "").upper())
    words = [w for w in re.split(r"\s+", fullname or "") if w]
    if not a or not words:
        return False
    initials = "".join(w[0] for w in words).upper()
    joined = "".join(words).upper()
    return (initials.startswith(a) or joined.startswith(a)
            or any(w.upper().startswith(a) for w in words)
            or (len(a) >= 3 and a[:2] == initials[:2] and a[2] == words[-1][0].upper()))


def _qty_dollars(qty, price):
    """Resting qty is in hundredths of a $1 contract; stake capacity for the taker."""
    try:
        return round(float(qty) / 100 * price, 2)
    except Exception:
        return None


def _best_price_for(bk, outcome_id, other_ids):
    """What a taker pays for `outcome_id`: 1 - best resting bid on the other side.

    Returns (probability, dollars available at that price)."""
    orders = (bk.get("book") or bk).get("orders") or {}
    best, qty = None, 0.0
    for oid in other_ids:
        ladder = orders.get(oid) or []
        if not ladder:
            continue
        p = float(ladder[0]["price"])                 # arrays are best-first
        q = sum(float(o.get("qty") or 0) for o in ladder if float(o["price"]) == p)
        if best is None or p > best:
            best, qty = p, q
    if best is None:
        return None, 0
    prob = 1 - best
    return prob, _qty_dollars(qty, prob)


def events_in_window(league, start, window_h=3):
    lo = int((start - timedelta(hours=window_h)).timestamp() * 1000)
    hi = int((start + timedelta(hours=window_h)).timestamp() * 1000)
    d = _get("/v3/catalog/events", f"league={league}&startsAfter={lo}&startsBefore={hi}&limit=500")
    return d.get("items") or []


def find_event(h):
    """Novig event for a board row: same two teams, start within 3h.

    Returns (event, home_name, away_name). Descriptions read 'Away @ Home'
    for US sports and 'A vs. B' for tennis."""
    league = league_for(h.get("league") or "")
    if not league or h.get("start") is None:
        return None, None, None
    parts = re.split(r"\s+vs\.?\s+", h.get("game") or "", maxsplit=1)
    want_a, want_b = parts[0], (parts[1] if len(parts) > 1 else "")
    for ev in events_in_window(league, h["start"]):
        if (ev.get("status") or "").upper() not in ("OPEN_PREGAME", "OPEN_INGAME", "DELAYED"):
            continue
        desc = ev.get("description") or ""
        m = re.match(r"(.+?)\s+(@|at|vs\.?)\s+(.+)$", desc)
        if not m:
            continue
        x, sep, y = m.group(1).strip(), m.group(2), m.group(3).strip()
        if not ((_team_match(want_a, x) and _team_match(want_b, y))
                or (_team_match(want_a, y) and _team_match(want_b, x))):
            continue
        home, away = (y, x) if sep.startswith("@") or sep == "at" else (None, None)
        return ev, home, away
    return None, None, None


def prices_for(h):
    """[(label, american_odds, dollars_available)] for the row's side, or []."""
    ev, home, away = find_event(h)
    if not ev:
        return []
    kind = _market_kind(h)
    mtype = market_type_for(kind)
    line = _line(h) if kind != "h2h" else None
    side = h.get("side") or ""
    d = _get("/v3/catalog/markets", f"event={ev['eventId']}&marketType={mtype}&limit=500")
    for m in d.get("items") or []:
        if (m.get("status") or "OPEN").upper() != "OPEN":
            continue
        if line is not None:
            st = m.get("strike")
            if st is None:
                continue
            st = float(st)
            if kind == "totals":
                if abs(st - abs(line)) > 0.01:
                    continue
            else:                                   # strike = HOME side's handicap
                side_is_home = bool(home) and _team_match(side, home)
                if abs(st - (line if side_is_home else -line)) > 0.01:
                    continue
        outs = m.get("outcomes") or []
        if len(outs) != 2:
            continue
        mine = None
        if kind == "totals":
            for o in outs:
                if (o.get("name") or "").lower().startswith(side.lower()):
                    mine = o
        else:
            # outcome names are abbreviations ('BUF -3.5'); resolve by matching
            # our team, else by eliminating the opponent
            other = away if (home and _team_match(side, home)) else home
            for o in outs:
                ab = (o.get("name") or "").split()[0]
                if _abbr_match(ab, side) or (home and away and
                                             _abbr_match(ab, home if _team_match(side, home) else away)):
                    mine = o
                    break
            if mine is None and other:
                rest = [o for o in outs if not _abbr_match((o.get("name") or "").split()[0], other)]
                mine = rest[0] if len(rest) == 1 else None
        if mine is None:
            continue
        others = [o["outcomeId"] for o in outs if o["outcomeId"] != mine["outcomeId"]]
        prob, avail = _best_price_for(book(m["marketId"]), mine["outcomeId"], others)
        if prob is None or not 0 < prob < 1:
            return []                               # listed, no resting liquidity
        return [(LABEL, american(prob), avail)]
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
def setup(mgmt_id, mgmt_pem, label="sharptracker", keep_trading=False, fund_amount=None):
    """One-time per environment: open a subaccount and key this Mac for it.

    Always: a `trading::read` key (quotes only). With keep_trading: the
    subaccount's `trading` key is kept too, so place.py can send orders — on
    Paper that is the point; on Production think twice. The management key is
    used only here and stored (id in Keychain, PEM beside the others) so
    `fund` can top the subaccount up later.
    """
    sfx = _sfx()
    mgmt = (mgmt_id, mgmt_pem)
    echo = call("POST", "/v3/echo", body={"ping": "sharptracker"}, key=mgmt)
    if echo.get("ping") != "sharptracker":
        raise RuntimeError(f"echo failed: {echo}")
    print(f"echo ok on {HOST} — management key, clock and signer are all good")

    HOME.mkdir(parents=True, exist_ok=True)
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
    if keep_trading:
        dest = HOME / KEY_FILES["trade"][1].format(sfx=sfx)
        dest.write_bytes(trade_pem.read_bytes()); dest.chmod(0o600)
        subprocess.run(["security", "add-generic-password", "-U", "-a", KEY_FILES["trade"][0] + sfx,
                        "-s", "novig", "-w", sub], check=True)
        print(f"TRADING key kept: keychain novig/trade-key-id{sfx}, PEM {dest}")
    else:
        print("trading key material destroyed (this Mac keeps read-only access only)")
    for f in (trade_pem, trade_pub):
        f.unlink()
    tmp.rmdir()

    read_pem = HOME / KEY_FILES["read"][1].format(sfx=sfx)
    pub = read_pem.with_suffix(".pub.pem")
    subprocess.run([OPENSSL, "genpkey", "-algorithm", "ed25519", "-out", str(read_pem)], check=True)
    read_pem.chmod(0o600)
    subprocess.run([OPENSSL, "pkey", "-in", str(read_pem), "-pubout", "-out", str(pub)], check=True)
    created = call("POST", f"/v3/account/subaccounts/{sub}/keys", key=mgmt, body={
        "name": f"{label}-read", "publicKey": pub.read_text(),
        "algorithm": "Ed25519", "scope": "trading::read"})
    kid = created.get("keyId")
    if not kid:
        raise RuntimeError(f"read key creation failed: {created}")
    subprocess.run(["security", "add-generic-password", "-U", "-a", KEY_FILES["read"][0] + sfx,
                    "-s", "novig", "-w", kid], check=True)
    print(f"read key created and stored: keychain novig/key-id{sfx}, PEM {read_pem}")

    mdest = HOME / KEY_FILES["mgmt"][1].format(sfx=sfx)
    if Path(mgmt_pem).resolve() != mdest.resolve():
        mdest.write_bytes(Path(mgmt_pem).read_bytes()); mdest.chmod(0o600)
    subprocess.run(["security", "add-generic-password", "-U", "-a", KEY_FILES["mgmt"][0] + sfx,
                    "-s", "novig", "-w", mgmt_id], check=True)
    print(f"management key stored: keychain novig/mgmt-key-id{sfx}, PEM {mdest}")
    if fund_amount:
        fund(fund_amount, sub=sub, mgmt=mgmt)
    print("now run:  python3 novig.py   (smoke test with the read key)")


def fund(amount, sub=None, mgmt=None, direction="fund"):
    """Move play/real money between the cash wallet and the subaccount."""
    mgmt = mgmt or _key("mgmt")
    sub = sub or _key("trade")[0]
    cid = f"{direction}-{sub[:8]}-{int(time.time())}"
    r = call("POST", f"/v3/account/subaccounts/{sub}/transfer", key=mgmt, body={
        "direction": direction, "amount": f"{float(amount):.5f}", "clientTransferId": cid})
    tid = r.get("transferId")
    for _ in range(10):
        if r.get("status") in ("Applied", "Rejected"):
            break
        time.sleep(1)
        r = call("GET", f"/v3/account/subaccounts/{sub}/transfers/{tid}", key=mgmt)
    print(f"transfer {direction} ${float(amount):.2f}: {r.get('status')}"
          + (f" (balance found {r.get('actualBalance')})" if r.get("status") == "Rejected" else ""))
    return r


def balance(sub=None):
    mgmt = _key("mgmt")
    sub = sub or _key("trade")[0]
    return call("GET", f"/v3/account/subaccounts/{sub}/balance", key=mgmt).get("balance")


# --------------------------------------------------------------------- cli
if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["setup"]:
        flags = [a for a in args if a.startswith("--")]
        pos = [a for a in args[1:] if not a.startswith("--")]
        if len(pos) < 2:
            print("usage: novig.py setup <MGMT_KEY_ID> <mgmt.pem> [label] [--trading] [--fund N]")
            sys.exit(1)
        famt = None
        for i, a in enumerate(args):
            if a == "--fund":
                famt = args[i + 1]; pos = [x for x in pos if x != famt]
        setup(pos[0], pos[1], *(pos[2:3]), keep_trading="--trading" in flags, fund_amount=famt)
        sys.exit(0)
    if args[:1] == ["fund"]:
        fund(args[1], direction=args[2] if len(args) > 2 else "fund")
        sys.exit(0)
    if args[:1] == ["balance"]:
        print(f"subaccount balance on {HOST}: ${float(balance()):.2f}")
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
        _key("read")
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
    ms = [m for mt in ("MONEY", "SPREAD", "TOTAL")
          for m in catalog_markets(league_for(league) or league, market_type=mt, limit=100)]
    print(f"{league}: {len(ms)} open moneyline/spread/total markets (first 100 of each)")
    for m in ms[:2] + ms[-2:]:
        bk = book(m["marketId"])
        orders = (bk.get("book") or bk).get("orders") or {}
        depth = {o.get("name"): len(orders.get(o["outcomeId"]) or []) for o in m.get("outcomes") or []}
        print(f"  {m.get('marketType'):>6} {m.get('strike') or '':>6} {m.get('description')}  depth={depth}")
