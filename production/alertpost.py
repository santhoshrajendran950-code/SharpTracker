#!/usr/bin/env python3
"""
Post the email alert's plays to X and Discord as ALERTS — not as graded picks.

    python3 alertpost.py /tmp/.sharp-plays.json          # post
    python3 alertpost.py /tmp/.sharp-plays.json --dry    # print, post nothing

Why this exists: on 2026-09-22 the email fired on a first-inning "Yes" at +127
(94 score, 9% hold) and nothing reached X or Discord, because the picks
pipeline only publishes what it can GRADE — a full-game market with a book
price. Santhosh wants the two channels to carry everything the email does.

So there are two kinds of post, and they must never be confused:

  PICK    picks.py. Priced at a book, unit-sized, settled nightly, on the
          record. Unchanged.
  ALERT   this file. Everything else the email fires on: no bettable price, a
          period market, a long dog, or below the pick gate. Posted with the
          trader's Polymarket entry, NO unit size, and the words "not graded".

A play that WILL become a pick (qualifies and has a book price) is skipped
here — the picks scan publishes it within ten minutes as a graded pick, and
posting it twice at two prices is the thing that got the Giants ML deleted.

Dedup is alerts-posted.jsonl, keyed the same way picks are.
"""
import json, os, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import anon, picks, postplays as P, units as U, xcard, xpost, discordsink as D
import bigboard as B

POSTED = os.path.join(HERE, "alerts-posted.jsonl")


def already():
    out = set()
    try:
        for l in open(POSTED):
            if l.strip():
                p = json.loads(l)
                out.add((p.get("slug"), p.get("market"), p.get("side")))
    except FileNotFoundError:
        pass
    return out


def will_be_pick(h, okey):
    """True if picks.py would publish this as a graded pick — leave it alone."""
    ok, _ = picks.qualifies(h)
    if not ok:
        return False, "below pick gate"
    bettable, _ = P.prices(h, okey, want=1)
    if not bettable:
        return False, "no book price"
    if picks.PRICE_WINDOW and bettable[0][1] > picks.PICK_MAX_DOG:
        return False, f"book price +{bettable[0][1]} is a long dog"
    return True, ""


def as_row(h, why):
    return {
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "slug": h.get("slug"), "league": h.get("league"),
        "game": picks.game_nick(h.get("game")), "title": h.get("game"),
        "market": h.get("market"), "side": h.get("side"),
        "pick": B.play_text(h), "conv": round(h.get("conv") or 0, 1),
        "sharp_entry": h.get("entry"), "alias": anon.alias(h.get("user")),
        "hold": h.get("hold"), "stake": h.get("stake"),
        "vs_typical": h.get("vs_typical"),
        "size_trusted": ((h.get("n_open") or 0) >= U.TRUST_POSITIONS
                         and (h.get("typical") or 0) >= U.TRUST_TYPICAL),
        "start": h.get("start"), "why_alert": why, "kind": "alert",
    }


def x_text(r):
    return (f"{r['pick']}   {B.chk.am(r.get('sharp_entry') or 0)} on Polymarket\n"
            f"{picks._game_line(r)}\n"
            f"Sharp money alert — not graded, no unit size ({r['why_alert']}).")


def discord_text(r):
    out = [f"ALERT  {r['pick']}   {B.chk.am(r.get('sharp_entry') or 0)} on Polymarket",
           f"       conv {r['conv']:.0f} · {r['alias']} · {anon.pct(r.get('hold'))} hold · "
           f"{anon.money(r.get('stake'))}"]
    vt = r.get("vs_typical")
    if vt and vt >= 2 and r.get("size_trusted"):
        out[-1] += f" · {vt:.0f}x their normal"
    out.append(f"       {r.get('market') or ''}")
    out.append(f"       {picks._game_line(r)}")
    out.append(f"       not graded, no unit size — {r['why_alert']}")
    return "\n".join(out)


def main():
    a = sys.argv[1:]
    dry = "--dry" in a
    files = [x for x in a if not x.startswith("--")]
    if not files:
        print(__doc__.strip().split("\n\n")[1])
        return 1
    try:
        rows = json.load(open(files[0]))
    except (FileNotFoundError, ValueError):
        return 0
    if not rows:
        return 0
    try:
        from oddskey import get_key
        okey = get_key()
    except Exception:
        okey = None
    seen = already() | {picks.key(p) for p in picks.load()}
    sent = skipped = 0
    for h in sorted(rows, key=lambda r: -(r.get("conv") or 0)):
        k = picks.key(h)
        if k in seen:
            skipped += 1
            continue
        pick_bound, why = will_be_pick(h, okey)
        if pick_bound:
            print(f"skip (picks.py will publish it): {B.play_text(h)}")
            continue
        r = as_row(h, why)
        text, dtext = x_text(r), discord_text(r)
        if dry:
            print(f"--- would post ---\n{text}\n\n{dtext}\n")
            continue
        card = f"/tmp/.xalert-{os.getpid()}.png"
        try:
            cok, cwhy = xcard.render_alert(r, card)
        except Exception as e:
            cok, cwhy = False, str(e)
        if not cok:
            print(f"alert card skipped: {cwhy}")
        ok, why_x, _ = xpost.post_with_image(text, card if cok else None)
        r["x_posted"] = bool(ok)
        if not ok:
            print(f"!! X alert FAILED: {why_x}")
        ch = D.channel_for("sharp", r.get("league") or "")
        dok, derr = D.send(ch, f"{r.get('league', '')} · ALERT {r['pick']}", dtext)
        r["d_posted"] = bool(dok)
        if not dok:
            print(f"!! Discord alert FAILED ({ch}): {derr}")
        if ok or dok:
            with open(POSTED, "a") as f:
                f.write(json.dumps(r) + "\n")
            seen.add(k)
            sent += 1
    print(f"alerts: {sent} posted, {skipped} already out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
