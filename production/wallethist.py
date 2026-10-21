#!/usr/bin/env python3
"""
Every tracked wallet's WHOLE wager history, as a persistent store.

Why this file exists. The board's size comparison used to be `vs_typical` =
stake / median of the wallet's CONCURRENTLY OPEN positions (lib/bt_rebuild.py
typical_at). That denominator is pathological at both ends and is the documented
cause of two bad reads on this board:

    Soarin22   held 2 open positions, so "1.0x typical" was its bet vs itself
    Feromont   median open position $24 across 100, so a routine $10,000 bet
               read as 414x and would have scored maximum size off pure noise

Santhosh (2026-10-20): "It should always be using the whole user history as
comparison vs the current wager size." So the comparison is now

    vs_hist = stake / median(every stake that wallet has ever placed)

which needs history the live sweep does not have -- a sweep sees open positions
at T, not a career. Hence a store.

STORAGE. Summary stats cannot be merged (you cannot update a median from a
median), and keeping every raw stake would be tens of MB. So each wallet keeps a
LOG-SPACED HISTOGRAM of its stakes: bucket = floor(BPL * log2(stake)), which is
~19% wide at BPL=4. Histograms merge by adding counts, so an update is additive
and order-free, and the median/percentiles are read back by interpolating inside
the bucket that crosses the half-count. Accuracy is far inside what a sizing
denominator needs, and the whole store for ~5k wallets is a few hundred KB.

PRIVACY. This file is keyed on wallet addresses and carries usernames, so it is
on the same footing as signals.jsonl and trusted.json: LOCAL ONLY, never synced.
docs/exclusions.md lists it. `--summary` emits the aggregate, wallet-free view
that is safe to commit.

  wallethist.py --build            seed from the nl-research windows (one-off)
  wallethist.py --summary          aggregate distribution, no wallets
  wallethist.py --show NAME        one wallet's history
  wallethist.py --stats            store size and coverage
"""
import glob
import json
import math
import os
import sys
import time

HOME = os.path.expanduser("~")
LIVE = os.path.join(HOME, ".nightly-line")
STORE = os.path.join(LIVE, "wallethist.json")
WINDOWS = os.path.join(HOME, "nl-research", "windows")

BPL = 16          # histogram buckets per log2 -- ~4.4% wide. At BPL=4 (~19%) the
                  # within-bucket log-uniform assumption cost 13% on wallets
                  # whose stakes pile on round numbers ($1,000 / $3,000 flat
                  # bettors), which is most of them. 16 puts the error under 3%.
MIN_N = 20        # below this a median is not believable (was TRUST_POSITIONS=10
                  # on open positions; a career median deserves a higher bar)
EXPERIENCED_N = 100  # Santhosh 2026-10-20: "compare to both median and average for
                  # experienced bettors". 100 wagers is the 90th percentile of
                  # history depth in the store (640 of 9,579 wallets) and the point
                  # where a MEAN is stable enough to quote -- a mean over 20 bets is
                  # one whale away from meaningless, which is why MIN_N alone is not
                  # the bar for the average.
MIN_MEDIAN = 250.0   # below this the median is real but trivially small
FLAT_SKEW = 1.25  # mean/median below this = a flat bettor, so median ~ mean and a
                  # multiple of either means the same thing. Above it the two
                  # disagree and BOTH must be exceeded before a bet is "unusual":
                  # measured skew is p50 1.66x, p90 3.95x, p99 12.94x, and only 21%
                  # of experienced wallets are near-flat.
RECENT_KEEP = 50  # raw stakes kept per wallet for form-relative features
VERSION = 2       # v2 adds slog/slog2/recent; v1 stores must be rebuilt


def _bucket(stake):
    if stake <= 0:
        return None
    return int(math.floor(BPL * math.log2(stake)))


def _bucket_lo(b):
    return 2.0 ** (b / float(BPL))


def _bucket_hi(b):
    return 2.0 ** ((b + 1) / float(BPL))


def load(path=STORE):
    try:
        with open(path) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {"version": VERSION, "updated": None, "wallets": {}}
    if "wallets" not in d:
        return {"version": VERSION, "updated": None, "wallets": {}}
    return d


def save(store, path=STORE):
    store["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(store, fh, separators=(",", ":"))
    os.replace(tmp, path)


def observe(store, wallet, stake, user=None, ts=None):
    """Add one placed wager to a wallet's history. Additive and order-free."""
    b = _bucket(stake)
    if b is None or not wallet:
        return False
    w = store["wallets"].setdefault(
        wallet, {"n": 0, "total": 0.0, "max": 0.0, "min": None,
                 "first_ts": None, "last_ts": None, "h": {}, "user": user,
                 "slog": 0.0, "slog2": 0.0, "recent": []})
    if user and not w.get("user"):
        w["user"] = user
    k = str(b)
    w["h"][k] = w["h"].get(k, 0) + 1
    w["n"] += 1
    w["total"] += float(stake)
    w["max"] = max(w["max"], float(stake))
    w["min"] = float(stake) if w["min"] is None else min(w["min"], float(stake))
    # log moments, for the standardised "how far outside their own spread" feature
    lg = math.log(float(stake))
    w["slog"] = w.get("slog", 0.0) + lg
    w["slog2"] = w.get("slog2", 0.0) + lg * lg
    # a short tail of the most recent wagers, for form-relative features. Kept as
    # raw stakes because RECENT is small; the histogram stays the career record.
    rec = w.setdefault("recent", [])
    rec.append(round(float(stake), 2))
    if len(rec) > RECENT_KEEP:
        del rec[:len(rec) - RECENT_KEEP]
    if ts:
        w["first_ts"] = ts if w["first_ts"] is None else min(w["first_ts"], ts)
        w["last_ts"] = ts if w["last_ts"] is None else max(w["last_ts"], ts)
    return True


def quantile(w, q=0.5):
    """Interpolated quantile out of the log-spaced histogram."""
    n = w.get("n") or 0
    if n <= 0:
        return None
    target = q * n
    seen = 0
    for b in sorted(int(k) for k in w["h"]):
        c = w["h"][str(b)]
        if seen + c >= target:
            lo, hi = _bucket_lo(b), _bucket_hi(b)
            frac = (target - seen) / float(c) if c else 0.0
            return lo * ((hi / lo) ** frac)
        seen += c
    return _bucket_hi(max(int(k) for k in w["h"]))


def log_moments(w):
    """(mu, sd) of log stake over the whole history, or (None, None)."""
    n = w.get("n") or 0
    if n < 2 or "slog" not in w:
        return None, None
    mu = w["slog"] / n
    var = max(0.0, w["slog2"] / n - mu * mu)
    return mu, math.sqrt(var)


def cume_below(w, stake):
    """Fraction of this wallet's career wagers strictly below `stake`.

    Read off the histogram: whole buckets below the target count fully, and the
    bucket containing it is apportioned log-linearly, the same assumption the
    quantile reader makes in the other direction.
    """
    n = w.get("n") or 0
    if n <= 0 or stake <= 0:
        return None
    b = _bucket(stake)
    below = 0
    for k, c in w["h"].items():
        kk = int(k)
        if kk < b:
            below += c
        elif kk == b:
            lo, hi = _bucket_lo(kk), _bucket_hi(kk)
            frac = math.log(stake / lo) / math.log(hi / lo) if hi > lo else 0.0
            below += c * min(1.0, max(0.0, frac))
    return below / float(n)


def summary(w):
    return {"n": w.get("n") or 0, "user": w.get("user"),
            "median": quantile(w, 0.5), "p25": quantile(w, 0.25),
            "p75": quantile(w, 0.75), "p90": quantile(w, 0.90),
            "mean": (w["total"] / w["n"]) if w.get("n") else None,
            "max": w.get("max"), "min": w.get("min"),
            "first_ts": w.get("first_ts"), "last_ts": w.get("last_ts")}


_CACHE = None


def _store():
    global _CACHE
    if _CACHE is None:
        _CACHE = load()
    return _CACHE


def _by_user(st):
    """user -> wallet key, built on demand. Live board rows carry `user` only
    (signals.jsonl has no wallet field), so lookups must work either way. A
    username that maps to several wallets resolves to the longest history."""
    idx = st.get("_user_idx")
    if idx is None:
        idx = {}
        for k, w in (st.get("wallets") or {}).items():
            u = (w.get("user") or "").lower()
            if not u:
                continue
            cur = idx.get(u)
            if cur is None or (st["wallets"][cur].get("n") or 0) < (w.get("n") or 0):
                idx[u] = k
        st["_user_idx"] = idx
    return idx


def find(key, store=None):
    """The history record for a wallet address or a username, or None."""
    st = store if store is not None else _store()
    ws = st.get("wallets") or {}
    if key in ws:
        return ws[key]
    k = _by_user(st).get((key or "").lower())
    return ws.get(k) if k else None


def rel(wallet, stake, store=None):
    """(vs_hist, median, n, trusted) for one wager against the whole history.

    `wallet` may be an address or a username. `trusted` is False when the
    history is too short or the median too small to be used as a denominator --
    the Soarin22 / Feromont cases above. Callers must cap rather than believe
    the multiple when it is False.
    """
    st = store if store is not None else _store()
    w = find(wallet, st)
    if not w or not w.get("n"):
        return None, None, 0, False
    med = quantile(w, 0.5)
    if not med:
        return None, None, w["n"], False
    ok = w["n"] >= MIN_N and med >= MIN_MEDIAN
    return (float(stake) / med if stake else None), med, w["n"], ok


def compare(key, stake, store=None):
    """The whole-history comparison for one wager, against BOTH centres.

    Returns a dict, or None when the wallet has no stored history:

      vs_median  stake / career median     (robust; ~1% accurate from the histogram)
      vs_mean    stake / career mean       (EXACT -- n and total are kept directly)
      skew       mean / median             the bettor's own style, not this bet
      flat       skew < FLAT_SKEW          a flat bettor: the two agree
      unusual    the bet clears BOTH       the only honest reading of "big for them"
      experienced n >= EXPERIENCED_N       is the mean worth quoting at all
      trusted    n >= MIN_N and median >= MIN_MEDIAN

    Why both. Stake distributions are right-skewed, so a bet at 3x the MEDIAN can
    sit at 1.2x the MEAN and be entirely routine for someone who sizes unevenly.
    Requiring both keeps a spread bettor's ordinary swing from reading as
    conviction, and for the 21% who are genuinely flat the two agree anyway.
    """
    st = store if store is not None else _store()
    w = find(key, st)
    if not w or not w.get("n"):
        return None
    n = w["n"]
    med = quantile(w, 0.5)
    mean = (w["total"] / n) if n else None
    if not med or not mean:
        return None
    vm, va = (float(stake) / med if stake else None), (float(stake) / mean if stake else None)
    skew = mean / med
    return {"vs_median": vm, "vs_mean": va, "median": med, "mean": mean,
            "n": n, "skew": skew, "flat": skew < FLAT_SKEW,
            "experienced": n >= EXPERIENCED_N,
            "trusted": n >= MIN_N and med >= MIN_MEDIAN,
            "unusual": bool(vm and va and vm >= 2.0 and va >= 2.0)}


def annotate(hits, store=None):
    """Stamp every board row with its whole-history comparison, in place.

    Adds vs_hist (stake / career median), hist_median, hist_n and hist_ok. Rows
    whose wallet has no stored history get None, and callers must treat that as
    "unknown", never as 1.0x. Safe to call on every sweep: it only reads.
    """
    st = store if store is not None else _store()
    for h in hits:
        c = compare(h.get("wallet") or h.get("user"), h.get("stake") or 0, st)
        if not c:
            h["vs_hist"] = h["hist_median"] = h["hist_mean"] = None
            h["vs_mean"] = h["hist_skew"] = None
            h["hist_n"], h["hist_ok"] = 0, False
            h["hist_experienced"] = h["hist_unusual"] = h["hist_flat"] = False
            continue
        h["vs_hist"], h["vs_mean"] = c["vs_median"], c["vs_mean"]
        h["hist_median"], h["hist_mean"] = c["median"], c["mean"]
        h["hist_n"], h["hist_ok"] = c["n"], c["trusted"]
        h["hist_skew"], h["hist_flat"] = c["skew"], c["flat"]
        h["hist_experienced"], h["hist_unusual"] = c["experienced"], c["unusual"]
    return hits


def build(verbose=True):
    """Seed the store from every scored research row. Idempotent: rebuilds."""
    files = sorted(glob.glob(os.path.join(WINDOWS, "*", "*", "bt_scored.jsonl")))
    files += sorted(glob.glob(os.path.join(WINDOWS, "20*", "bt_scored.jsonl")))
    store = {"version": VERSION, "updated": None, "wallets": {}}
    seen = set()
    rows = kept = 0
    for i, f in enumerate(files, 1):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rows += 1
                k = (r.get("wallet"), r.get("slug"), r.get("asset"), r.get("t_ts"))
                if k in seen:
                    continue
                seen.add(k)
                if observe(store, r.get("wallet"), r.get("stake") or 0,
                           r.get("user"), r.get("t_ts")):
                    kept += 1
        if verbose and i % 20 == 0:
            print("  %d/%d files · %d wallets" % (i, len(files), len(store["wallets"])),
                  file=sys.stderr)
    save(store)
    if verbose:
        print("built %s — %d rows read, %d wagers kept, %d wallets"
              % (STORE, rows, kept, len(store["wallets"])))
    return store


def main(argv):
    if "--build" in argv:
        build()
        return 0
    st = load()
    ws = st.get("wallets") or {}
    if "--stats" in argv:
        print("store   %s" % STORE)
        print("updated %s" % st.get("updated"))
        print("wallets %d · wagers %d · bytes %s"
              % (len(ws), sum(w["n"] for w in ws.values()),
                 os.path.getsize(STORE) if os.path.exists(STORE) else 0))
        usable = [w for w in ws.values()
                  if w["n"] >= MIN_N and (quantile(w, 0.5) or 0) >= MIN_MEDIAN]
        print("usable as a denominator (n>=%d, median>=$%.0f): %d (%.1f%%)"
              % (MIN_N, MIN_MEDIAN, len(usable),
                 100.0 * len(usable) / len(ws) if ws else 0))
        return 0
    if "--show" in argv:
        name = argv[argv.index("--show") + 1].lower()
        hits = [(k, w) for k, w in ws.items()
                if k.lower() == name or (w.get("user") or "").lower() == name]
        if not hits:
            print("no wallet matching %r" % name)
            return 1
        for k, w in hits:
            s = summary(w)
            print("%s  %s" % (s["user"] or "—", k))
            print("  n=%d  median $%,.0f  p25 $%,.0f  p75 $%,.0f  p90 $%,.0f"
                  .replace(",", "") % (s["n"], s["median"] or 0, s["p25"] or 0,
                                       s["p75"] or 0, s["p90"] or 0))
            print("  mean $%.0f  min $%.0f  max $%.0f"
                  % (s["mean"] or 0, s["min"] or 0, s["max"] or 0))
        return 0
    if "--summary" in argv:
        import statistics as stt
        meds = sorted(q for q in (quantile(w, 0.5) for w in ws.values()) if q)
        ns = sorted(w["n"] for w in ws.values())
        L = ["# Wallet wager-history store — aggregate\n",
             "No wallet address or username appears here; see docs/exclusions.md.\n",
             "| Metric | Value |", "|---|---:|",
             "| Wallets | %d |" % len(ws),
             "| Wagers recorded | %d |" % sum(ns),
             "| Wallets with n >= %d | %d |" % (MIN_N, sum(1 for n in ns if n >= MIN_N)),
             "| Median wallet history length | %d wagers |" % (stt.median(ns) if ns else 0),
             "| Median of wallet medians | $%.0f |" % (stt.median(meds) if meds else 0),
             ""]
        L.append("Distribution of per-wallet median wager:\n")
        L.append("| Percentile | Median wager |")
        L.append("|---|---:|")
        for p in (10, 25, 50, 75, 90, 99):
            if meds:
                L.append("| p%d | $%.0f |" % (p, meds[min(len(meds) - 1, int(len(meds) * p / 100))]))
        print("\n".join(L))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
