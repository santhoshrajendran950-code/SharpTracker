#!/usr/bin/env python3
"""
Turn screen_results.json into the markdown tables for the EXP-033 write-up.

    python3 screen_md.py > screen_tables.md

Generated rather than transcribed, so the numbers in the repo cannot drift from
the numbers the screen actually produced.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(HERE, "screen_results.json")))


def ci(s):
    c = s.get("ci")
    if not c:
        return "n/a"
    lo, hi = (float(c[0]), float(c[1])) if isinstance(c, (list, tuple)) else (0, 0)
    return f"[{lo:+.0f}, {hi:+.0f}]"


def num(x, f="{:+.1f}"):
    return "—" if x is None else f.format(float(x))


def row(label, s, extra=""):
    if not s:
        return f"| {label} | — | — | — | — | — | — |{extra}"
    return (f"| {label} | {s['n']} / {s['games']} | {s['wins']}-{s['n']-s['wins']} | "
            f"{num(s['winrate'], '{:.1f}')}% | **{num(s['roi'])}%** {ci(s)} | "
            f"{num(s['clv_pm'], '{:+.2f}')} ({num(s['clv_pm_pos'], '{:.0f}')}%) | "
            f"{num(s['clv_book'], '{:+.2f}')} (n={s['clv_book_n']}) |{extra}")


HDR = ("| | n / games | W-L | win | ROI, flat 1u [95% CI] | CLV Polymarket (% +) | "
       "CLV book |\n|---|---|---|---|---|---|---|")

out = []

# ----------------------------------------------------------------- census
c = R.get("census") or {}
out.append("### Census\n")
out.append("| sport | slates | games | candidate positions | frozen-v4 qualifiers | "
           "median positions / slate | wallets | span |")
out.append("|---|---|---|---|---|---|---|---|")
for sp in ("NFL", "NCAAF"):
    v = c.get(sp)
    if not v:
        out.append(f"| {sp} | — | — | — | — | — | — | — |")
        continue
    out.append(f"| {sp} | {v['slates']} | {v['games']} | {v['candidates']} | "
               f"{v['qualifying']} | {float(v['median_per_slate']):.0f} | {v['wallets']} | "
               f"{v['dates'][0]} .. {v['dates'][-1]} |")
out.append("")
for sp in ("NFL", "NCAAF"):
    v = c.get(sp)
    if v:
        dow = ", ".join(f"{k} {n}" for k, n in sorted(v["dow"].items(), key=lambda kv: -kv[1]))
        out.append(f"- **{sp}** slate days: {dow}."
                   + ("  **Under ~150 candidate positions — every result below is "
                      "directional only.**" if v["candidates"] < 150 else ""))
out.append("")

# ----------------------------------------------------------------- per sport
for sp in ("NFL", "NCAAF"):
    s = R.get(sp)
    if not s:
        continue
    out.append(f"### {sp}\n")
    exd, hod = s["explore_slates"], s["holdout_slates"]
    out.append(f"Exploration {len(exd)} slates ({exd[0]} .. {exd[-1]}) · "
               f"holdout {len(hod)} slates ({hod[0]} .. {hod[-1]}). "
               f"{s['n_configs']} configurations evaluated, {s['n_cands']} reportable.\n")
    nb = s.get("null_best") or {}
    if nb:
        out.append(f"**Selection calibration.** With {s['n_cands']} reportable rules and no "
                   f"rule having any edge, the *best* of them returns "
                   f"**{num(nb['p50'])}%** at the median, {num(nb['p90'])}% at the 90th "
                   f"percentile and {num(nb['p95'])}% at the 95th "
                   f"({nb['reps']} replications). Any exploration ROI at or below the 90th "
                   f"percentile is indistinguishable from picking the luckiest rule.\n")
    out.append("**Baselines**\n")
    out.append(HDR)
    for lab, key in (("(a) unfiltered — exploration", "(a) every candidate, unfiltered"),
                     ("(b) frozen v4 filter — exploration", "(b) frozen v4 production filter")):
        out.append(row(lab, (s["baselines"].get(key) or {}).get("ex")))
    for lab, key in (("(a) unfiltered — **holdout**", "(a) every candidate, unfiltered"),
                     ("(b) frozen v4 filter — **holdout**", "(b) frozen v4 production filter")):
        out.append(row(lab, (s["baselines"].get(key) or {}).get("ho")))
    out.append("")
    out.append("**Top 3 by exploration, holdout reported once**\n")
    out.append(HDR)
    for t in s["top3"]:
        out.append(row(f"`{t['label']}` — exploration", t["ex"]))
        flag = ""
        if t.get("ho") and t["ho"]["n"] < 100:
            flag = " *underpowered*"
        out.append(row(f"`{t['label']}` — **holdout**", t.get("ho"), flag))
    out.append("")

print("\n".join(out))
