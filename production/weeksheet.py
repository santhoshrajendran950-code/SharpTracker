#!/usr/bin/env python3
"""
Weekly bet ledger as a spreadsheet — full ledger left, summary right.

    python3 weeksheet.py                 # the week that just ended (Mon-Sun)
    python3 weeksheet.py --week 2026-09-14
    python3 weeksheet.py --all           # every bet ever logged
    python3 weeksheet.py --title         # print the sheet title and exit
    python3 weeksheet.py --xlsx out.xlsx # EVERY week as its own named page

With --xlsx, writes one workbook containing one PAGE PER WEEK, each named for
that week's Monday (e.g. "2026-09-14"), newest first. The whole workbook is
rebuilt from bets.jsonl every run, so it is idempotent and self-healing — a
missed Monday repairs itself on the next one, and a corrected bet propagates.
The Drive MCP exposes Drive and not Sheets, so a tab cannot be appended to a
live spreadsheet; replacing the file wholesale is the only route to named pages.

Emits CSV on stdout. Columns A-H are the ledger, one row per bet. Columns J-K
carry the summary, so it lands to the RIGHT of the ledger in the rendered sheet
rather than underneath it.

Weeks run Monday 00:00 through Sunday 23:59 local, and the report is generated
the following Monday at 08:00 — so it always describes a week that is fully
closed, never one still in progress.

PRIVATE. This reads bets.jsonl, which is Santhosh's own money. The output goes
to ONE destination: a spreadsheet in his own Drive. It must never be attached to
an alert email (those have BCC'd recipients) or posted to a channel —
discordsink.py refuses anything carrying these markers for exactly that reason.
Open bets are listed but excluded from every settled statistic, so an unsettled
wager can never flatter the ROI.
"""
import csv, io, json, os, sys
from collections import defaultdict
from datetime import datetime, date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BETS = os.path.join(HERE, "bets.jsonl")


def load():
    try:
        return [json.loads(l) for l in open(BETS) if l.strip()]
    except FileNotFoundError:
        return []


def week_bounds(ref=None):
    """The most recent COMPLETE Mon-Sun week relative to `ref` (default today)."""
    ref = ref or date.today()
    monday_this = ref - timedelta(days=ref.weekday())
    start = monday_this - timedelta(days=7)
    return start, start + timedelta(days=6)


def ret_of(b):
    r = b.get("result")
    if r == "won":
        return b["to_win"]
    if r == "lost":
        return -b["risk"]
    return None          # open — never counted


def summarize(rows):
    settled = [b for b in rows if b.get("result") in ("won", "lost")]
    open_b = [b for b in rows if b.get("result") not in ("won", "lost")]
    risked = sum(b["risk"] for b in settled)
    pl = sum(ret_of(b) for b in settled)
    w = sum(1 for b in settled if b["result"] == "won")
    out = [
        ("SUMMARY", ""),
        ("Bets settled", len(settled)),
        ("Record", f"{w}-{len(settled)-w}"),
        ("Win rate", f"{w/len(settled)*100:.1f}%" if settled else "—"),
        ("Risked", f"${risked:,.2f}"),
        ("Net P/L", f"${pl:+,.2f}"),
        ("ROI", f"{pl/risked*100:+.1f}%" if risked else "—"),
        ("Avg stake", f"${risked/len(settled):,.2f}" if settled else "—"),
        ("Open (not counted)", len(open_b)),
        ("", ""),
        ("BY TYPE", ""),
    ]
    for k in ("single", "parlay"):
        s = [b for b in settled if b.get("kind") == k]
        if not s:
            continue
        rr = sum(b["risk"] for b in s)
        p = sum(ret_of(b) for b in s)
        ww = sum(1 for b in s if b["result"] == "won")
        out.append((f"{k} record", f"{ww}-{len(s)-ww}"))
        out.append((f"{k} P/L", f"${p:+,.2f}"))
        out.append((f"{k} ROI", f"{p/rr*100:+.1f}%" if rr else "—"))
    out += [("", ""), ("BY BOOK", "")]
    by = defaultdict(lambda: [0.0, 0.0, 0, 0])
    for b in settled:
        k = by[b.get("book") or "—"]
        k[0] += b["risk"]
        k[1] += ret_of(b)
        k[2] += b["result"] == "won"
        k[3] += b["result"] == "lost"
    for bk, k in sorted(by.items(), key=lambda kv: -kv[1][1]):
        out.append((bk, f"{k[2]}-{k[3]}  ${k[1]:+,.2f}"))

    # The honesty line. A week of bets on one game is one outcome, and an ROI
    # computed as though it were many will read as a track record when it is not.
    games = len({b.get("game") for b in settled})
    out += [("", ""), ("INDEPENDENCE", ""),
            ("Distinct games", games),
            ("Settled bets", len(settled))]
    if settled and games and len(settled) / games >= 2:
        out.append(("!! WARNING",
                    f"{len(settled)} bets over only {games} game(s) — correlated, "
                    f"treat ROI as one or two outcomes, not {len(settled)}"))
    return out


def grid(sel):
    """(rows) for one week: ledger in A-H, gutter in I, summary in J-K."""
    summary = summarize(sel)
    head = ["Date", "Result", "Pick", "Type", "Book", "Odds", "Risk", "Return"]
    body = []
    for b in sorted(sel, key=lambda x: (x.get("date", ""), x.get("logged", ""))):
        r = ret_of(b)
        body.append([b.get("date", ""), b.get("result") or "open",
                     b.get("pick", ""), b.get("kind", ""), b.get("book", ""),
                     int(b.get("odds", 0)), round(b["risk"], 2),
                     round(r, 2) if r is not None else ""])
    rows = [head + ["", "", ""]]
    for i in range(max(len(body), len(summary))):
        left = body[i] if i < len(body) else [""] * 8
        right = summary[i] if i < len(summary) else ("", "")
        rows.append(left + ["", str(right[0]), str(right[1])])
    return rows


def all_weeks(rows):
    """[(monday_date, bets)] for every week present, newest first."""
    buckets = defaultdict(list)
    for b in rows:
        try:
            d = datetime.strptime(b["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        buckets[d - timedelta(days=d.weekday())].append(b)
    return sorted(buckets.items(), key=lambda kv: kv[0], reverse=True)


def main():
    a = sys.argv[1:]
    rows = load()
    if "--xlsx" in a:
        from xlsxlite import write_workbook
        dest = a[a.index("--xlsx") + 1]
        weeks = all_weeks(rows)
        if not weeks:
            print("no bets to write", file=sys.stderr)
            return 1
        write_workbook(dest, [(m.isoformat(), grid(bs)) for m, bs in weeks])
        print(f"{dest}: {len(weeks)} page(s) — "
              + ", ".join(m.isoformat() for m, _ in weeks))
        return 0
    if "--all" in a:
        sel, label = rows, "all time"
    else:
        if "--week" in a:
            ref = datetime.strptime(a[a.index("--week") + 1], "%Y-%m-%d").date()
            lo = ref - timedelta(days=ref.weekday())
            hi = lo + timedelta(days=6)
        else:
            lo, hi = week_bounds()
        sel = [b for b in rows if lo.isoformat() <= b.get("date", "") <= hi.isoformat()]
        label = f"{lo:%b %-d} - {hi:%b %-d, %Y}"

    title = f"Nightly Line - Bet Ledger - {label}"
    if "--title" in a:
        print(title)
        return 0

    sel.sort(key=lambda b: (b.get("date", ""), b.get("logged", "")))
    summary = summarize(sel)

    head = ["Date", "Result", "Pick", "Type", "Book", "Odds", "Risk", "Return"]
    body = []
    for b in sel:
        r = ret_of(b)
        body.append([b.get("date", ""), b.get("result") or "open",
                     b.get("pick", ""), b.get("kind", ""), b.get("book", ""),
                     f"{b.get('odds', 0):+d}", f"{b['risk']:.2f}",
                     f"{r:+.2f}" if r is not None else ""])

    out = io.StringIO()
    w = csv.writer(out)
    # Ledger in A-H, summary in J-K. Column I is left empty as the gutter, which
    # is what puts the summary to the RIGHT rather than below.
    w.writerow(head + ["", "", ""])
    n = max(len(body), len(summary))
    for i in range(n):
        left = body[i] if i < len(body) else [""] * 8
        right = summary[i] if i < len(summary) else ("", "")
        w.writerow(left + ["", str(right[0]), str(right[1])])
    sys.stdout.write(out.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(main())
