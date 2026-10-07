#!/usr/bin/env python3
"""
Shared plumbing for the out-of-sample backtest.

Three rules this module exists to enforce, because getting any of them wrong
makes the whole backtest quietly wrong rather than loudly broken:

1. NEVER take the sweep lock, but never send a data-api request while another
   process holds it. windows.py sweeps every 240s and the live alerts depend on
   it; a backtest that grabs the lock makes the alerting skip cycles, and one
   that ignores it doubles the request rate into a throttled endpoint.
2. NEVER swallow a failure. Partial data is indistinguishable from "no signal",
   which is the single most dangerous failure mode in this whole system. Every
   stage prints N/M and writes its failure count into its output file.
3. NEVER write to a live ledger. Everything here lands under backtest/.
"""
import json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # nl-research: frozen copies in lib/
import check as chk                      # noqa: E402  the live scoring module

HERE = os.path.dirname(os.path.abspath(__file__))
CONCURRENCY = 6                          # data-api: the tuned-down, polite rate
LOCK_POLL, LOCK_MAX = 2.0, 600.0

STATS = {"ok": 0, "fail": 0, "lock_waits": 0, "lock_seconds": 0.0}
_slock = threading.Lock()


def _bump(k, n=1):
    with _slock:
        STATS[k] += n


def wait_for_sweep():
    """Block while windows.py (or a board run) holds the lock. Never take it."""
    if not os.path.exists(chk.SWEEP_LOCK):
        return
    t0 = time.time()
    _bump("lock_waits")
    while os.path.exists(chk.SWEEP_LOCK) and time.time() - t0 < LOCK_MAX:
        time.sleep(LOCK_POLL)
    _bump("lock_seconds", time.time() - t0)


def get(url, retries=chk.RETRIES):
    """Lock-aware, counted fetch. Returns None on failure — and counts it."""
    wait_for_sweep()
    r = chk.get(url, retries=retries)
    _bump("ok" if r is not None else "fail")
    return r


def pmap(fn, items, concurrency=CONCURRENCY, label="", every=25):
    """Bounded-concurrency map with an N/M progress line. Failures are the
    caller's to record; this only reports progress and never hides an item."""
    out, n, total = [], 0, len(items)
    every = max(1, min(every, total // 10 or 1))   # always ~10 progress lines
    lk = threading.Lock()
    with ThreadPoolExecutor(concurrency) as ex:
        for r in ex.map(fn, items):
            out.append(r)
            with lk:
                n += 1
            if label and (n % every == 0 or n == total):
                print(f"  {label}: {n}/{total}", flush=True)
    return out


def path(name):
    return os.path.join(HERE, name)


def save(name, obj):
    p = path(name)
    with open(p, "w") as f:
        json.dump(obj, f)
    print(f"  wrote {p} ({os.path.getsize(p):,} bytes)", flush=True)
    return p


def load(name):
    with open(path(name)) as f:
        return json.load(f)


def save_jsonl(name, rows):
    p = path(name)
    with open(p, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"  wrote {p} ({len(rows):,} rows)", flush=True)
    return p


def load_jsonl(name):
    with open(path(name)) as f:
        return [json.loads(l) for l in f if l.strip()]


def report_stats(tag):
    s = STATS
    tot = s["ok"] + s["fail"]
    print(f"[{tag}] requests {s['ok']}/{tot} ok, {s['fail']} FAILED"
          + (f" · waited {s['lock_seconds']:.0f}s on {s['lock_waits']} sweep locks"
             if s["lock_waits"] else ""), flush=True)
    if s["fail"]:
        print(f"[{tag}] !! {s['fail']} request(s) failed — results are PARTIAL", flush=True)
    return dict(s)
