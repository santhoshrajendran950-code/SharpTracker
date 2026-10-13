"""Client-side pacing for data-api.polymarket.com.

Measured 2026-10-13 (research in exchange/alert-rules notes): the positions
endpoint is a token bucket of ~200 requests that refills at ~17/s, keyed per
client, independent of concurrency. 150 cold requests all pass; the next 100
all 429; 8 threads 429 exactly as hard as 24. Our sweeps fired ~90/s and then
leaned on 5 exponential retries (~12 s) to ride the refill, which worked only
while nothing else drained the bucket. catalyst.py (300 wallets, no lock)
finishing a minute before a board run was enough for 22-31 wallets to exhaust
their retries.

This bucket is slightly smaller and slower than the server's so a single
process never trips it: 150 tokens, 15/s. Two processes still sum to 30/s, so
every sweep must ALSO hold check.sweep_lock. Use: pace.wait(url) before each
curl attempt; it returns immediately for other hosts.

A process starts with a SMALL burst, not a full one: a board that takes the
lock the moment windows.py releases it meets a server bucket that is already
empty, and a 150-request opening burst then cost 14 wallets their retries
(17:14 2026-10-13, measured). And on any 429 the server has told us its
bucket is empty, so penalize() zeroes ours — from then on we trickle at RATE.
"""
import threading
import time

PACED_HOST = "data-api.polymarket.com"
CAPACITY, RATE, START = 150.0, 15.0, 20.0

_lock = threading.Lock()
_tokens = START
_last = time.monotonic()
_hits = 0


def penalize():
    """Call on a 429: the server bucket is empty, so ours is too."""
    global _tokens, _hits
    with _lock:
        _tokens = 0.0
        _hits += 1


def throttled():
    """How many 429s this process has seen (for the header line)."""
    return _hits


def wait(url):
    """Block until a token is available for a data-api request."""
    global _tokens, _last
    if PACED_HOST not in url:
        return
    while True:
        with _lock:
            now = time.monotonic()
            _tokens = min(CAPACITY, _tokens + (now - _last) * RATE)
            _last = now
            if _tokens >= 1:
                _tokens -= 1
                return
            need = (1 - _tokens) / RATE
        time.sleep(need)
