"""
Polymarket Data API v1 -> v2 shim.

v1 retired 2026-11-02 ~15:20 EST (every /positions and /v1/leaderboard call
now answers 410), and every scan since returned zero wallets. v2 differs in
four ways that matter here:

    envelope    rows sit under "data"; paginated routes add "pagination"
    paging      cursor only; `offset` is a 400, even offset=0
    casing      snake_case rows (proxy_wallet), and some real renames:
                user_id->proxyWallet, volume->vol, current_size->size,
                current_price->curPrice, token_id->asset
    params      market->condition, timePeriod=ALL->time_period=all,
                orderBy->sort_by, sortBy=CURRENT->sort_by=CURRENT_VALUE,
                sizeThreshold->filter_type=TOKENS&filter_amount

Rather than touch thirteen callers' field names, each get() hands v1 URLs here
and gets back exactly what v1 returned: bare lists of camelCase rows. The
pick logic never sees v2.

    if dataapi.is_v1(url):
        return dataapi.fetch(url, get)

`raw` is the caller's own get(): pacing, retries and failure counters stay
where they were. v2 URLs never match is_v1, so the call cannot recurse.

Semantics kept from v1, not just names:
  initialValue = size x avgPrice   (v1's stake; v2's entry_cost_usdc agrees to
                                    the cent but is fee-exclusive by contract)
  cashPnl      = currentValue - initialValue
  leaderboard user lookup -> [row] or [], rank = rank on the requested board
Contract: https://data-api.polymarket.com/v2/openapi.json
"""
import re, threading, time
from urllib.parse import urlsplit, parse_qsl, urlencode

HOST = "data-api.polymarket.com"
BASE = f"https://{HOST}/v2"
LB_CACHE_S = 300         # one board fetch serves every offset page of a sweep
LB_MAX = 1000            # v2 page cap

_V1 = re.compile(r"^/(v1/leaderboard|positions|activity|holders)/?$")
_lock = threading.Lock()
_boards = {}             # (category, period, sort) -> (fetched_at, rows)

SORT_POS = {"CURRENT": "CURRENT_VALUE", "TOKENS": "TOKENS", "PRICE": "PRICE",
            "CASHPNL": "UNREALIZED_PNL"}
SORT_LB = {"PNL": "PNL", "VOL": "VOLUME", "VOLUME": "VOLUME"}


def is_v1(url):
    u = urlsplit(url)
    return u.netloc == HOST and bool(_V1.match(u.path))


def _camel(k):
    head, *rest = k.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def _row(r, rename=None):
    out = {}
    for k, v in r.items():
        out[(rename or {}).get(k) or _camel(k)] = v
    return out


def _data(js):
    return js.get("data") if isinstance(js, dict) else None


# ---- leaderboard -------------------------------------------------------------
LB_RENAME = {"user_id": "proxyWallet", "volume": "vol", "verified": "verifiedBadge"}


def _lb_row(r, sort):
    out = _row(r, LB_RENAME)
    if "rankPnl" in out or "rankVolume" in out:          # single-user lookup shape
        out["rank"] = out.get("rankVolume" if sort == "VOLUME" else "rankPnl")
    return out


def _board(raw, cat, period, sort, need):
    """The top `need` rows of one board, cursor-walked once and cached."""
    key = (cat, period, sort)
    with _lock:
        hit = _boards.get(key)
        if hit and time.time() - hit[0] < LB_CACHE_S and len(hit[1]) >= need:
            return hit[1]
    rows, cursor = [], None
    while len(rows) < need:
        q = {"category": cat, "time_period": period, "sort_by": sort}
        # a full page up front: the 690-wallet pool is 14 offset pages, one request
        q.update({"cursor": cursor} if cursor else {"limit": LB_MAX})
        js = raw(f"{BASE}/leaderboard?{urlencode(q)}")
        page = _data(js)
        if not isinstance(page, list):
            if not rows:
                return None              # first page failed: v1 callers see None
            break
        rows.extend(page)
        cursor = ((js.get("pagination") or {}).get("next_cursor"))
        if not cursor or not page:
            break
    with _lock:
        _boards[key] = (time.time(), rows)
    return rows


def _leaderboard(q, raw):
    cat = (q.get("category") or "overall").lower()
    period = (q.get("timePeriod") or q.get("time_period") or "day").lower()
    sort = SORT_LB.get((q.get("orderBy") or q.get("sort_by") or "PNL").upper(), "PNL")
    if q.get("user"):
        js = raw(f"{BASE}/leaderboard?" + urlencode(
            {"category": cat, "time_period": period, "sort_by": sort, "user": q["user"]}))
        if not isinstance(js, dict):
            return None
        d = js.get("data")
        return [_lb_row(d, sort)] if isinstance(d, dict) else []
    off, lim = int(q.get("offset") or 0), int(q.get("limit") or 25)
    rows = _board(raw, cat, period, sort, off + lim)
    if rows is None:
        return None
    return [_lb_row(r, sort) for r in rows[off:off + lim]]


# ---- positions ---------------------------------------------------------------
POS_RENAME = {"token_id": "asset", "current_size": "size", "current_price": "curPrice",
              "total_size": "totalBought", "opposite_token_id": "oppositeAsset"}


def _pos_row(r):
    out = _row(r, POS_RENAME)
    size, avg = out.get("size") or 0.0, out.get("avgPrice") or 0.0
    out["initialValue"] = size * avg
    out["cashPnl"] = (out.get("currentValue") or 0.0) - out["initialValue"]
    return out


def _positions(q, raw):
    v2 = {"user": q.get("user"), "limit": min(int(q.get("limit") or 100), 1000)}
    if q.get("market"):
        v2["condition"] = q["market"]
    if q.get("eventId"):
        v2["event_id"] = q["eventId"]
    s = SORT_POS.get((q.get("sortBy") or "").upper())
    if s:
        v2["sort_by"] = s
    if q.get("sortDirection"):
        v2["sort_direction"] = q["sortDirection"].upper()
    if q.get("sizeThreshold"):
        v2.update(filter_type="TOKENS", filter_amount=q["sizeThreshold"])
    js = raw(f"{BASE}/positions?" + urlencode({k: v for k, v in v2.items() if v is not None}))
    d = _data(js)
    return [_pos_row(r) for r in d] if isinstance(d, list) else None


# ---- activity / holders ------------------------------------------------------
def _activity(q, raw):
    v2 = {"user": q.get("user"), "limit": min(int(q.get("limit") or 100), 1000)}
    for a, b in (("market", "condition"), ("type", "type"), ("side", "side"),
                 ("start", "start"), ("end", "end"), ("sortDirection", "sort_direction")):
        if q.get(a):
            v2[b] = q[a]
    js = raw(f"{BASE}/activity?" + urlencode({k: v for k, v in v2.items() if v is not None}))
    d = _data(js)
    return [_row(r, {"token_id": "asset"}) for r in d] if isinstance(d, list) else None


def _holders(q, raw):
    v2 = {"condition": q.get("market") or q.get("condition"),
          "limit": min(int(q.get("limit") or 100), 1000)}
    if q.get("minBalance"):
        v2["min_balance"] = q["minBalance"]
    js = raw(f"{BASE}/holders?" + urlencode(v2))
    d = _data(js)
    if not isinstance(d, list):
        return None
    return [{"token": g.get("token_id"),
             "holders": [_row(h, {"token_id": "asset"}) for h in g.get("holders") or []]}
            for g in d]


ROUTES = {"v1/leaderboard": _leaderboard, "positions": _positions,
          "activity": _activity, "holders": _holders}


def fetch(url, raw):
    """v1 URL -> v1-shaped result (list, or None on failure) via v2."""
    u = urlsplit(url)
    q = dict(parse_qsl(u.query))
    return ROUTES[_V1.match(u.path).group(1)](q, raw)
