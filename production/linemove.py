#!/usr/bin/env python3
"""
Line-move tier — which way the market is moving on a position (EXP-030).

The one thing in 99 research sport-months that ordered anything out of
sample: a ridge regression on signal CLV (close − entry, pts) whose only
material input is how far the line has already moved toward the bet since
the sharp entered (+2.8 pts per sd). Test quintiles ran −3.15 → +2.55 pts of
CLV and −7.3% → −1.3% ROI at T-60; the bottom 60% lost 6–7% in every quintile.
It predicts the MOVE, not the outcome: the top tier sits at the closing price
minus vig. Its value is exclusion. See docs/reviews/2026-10-07-conviction-v5.md.

Coefficients, standardisation and quintile cuts come from linemove_v5.json
(fitted by research/score_v5.py). Pure stdlib: the launchd python has no numpy.

Live rows carry lifetime hold/P-L where research had 90-day priors, and no
point-in-time trader CLV; those terms are ≈0.03 pts per sd, so the live score
is the drift term plus noise. Documented, accepted.

    tier(h) -> ("toward" | "flat" | "away", pts)
"""
import json, math, os

HERE = os.path.dirname(os.path.abspath(__file__))
_M = None


def _model():
    global _M
    if _M is None:
        _M = json.load(open(os.path.join(HERE, "linemove_v5.json")))
    return _M


def features(h):
    vt = h.get("vs_typical") if h.get("typical") and (h.get("n_open") or 0) >= 10 else 1.0
    now, entry = h.get("now") or 0, h.get("entry") or 0
    return [
        math.log1p(max(h.get("hold") or 0, 0)),
        math.log10(max(h.get("pnl") or 1, 1)),
        math.log10(max(h.get("stake") or 1, 1)),
        math.log1p(max(vt or 1.0, 0.01)),
        math.log1p(h.get("n_open") or 0),
        1.0 if h.get("fresh") else 0.0,
        min(float(h.get("stack") or 1), 4) - 1,
        min(float(h.get("consensus") or 1), 5) - 1,
        (now - entry) * 10 if now and entry else 0.0,
        0.0,            # trader prior CLV: not available live
        1.0,            # "no CLV history" indicator
    ]


def predict(h):
    """Expected close − entry, in pts."""
    m = _model(); x = features(h)
    return m["intercept"] + sum((xi - mu) / sd * w for xi, mu, sd, w in zip(x, m["mu"], m["sd"], m["w"]))


def tier(h):
    m = _model(); p = predict(h); c = m["quintile_cuts"]
    t = "toward" if p > c[2] else "away" if p <= c[1] else "flat"
    return t, round(p, 2)


def label(h):
    t = h.get("move_tier")
    if not t:
        return "—"
    return f"{t} {h.get('move', 0):+.1f}"


if __name__ == "__main__":
    import sys
    print(json.dumps(_model(), indent=1)[:600])
