#!/usr/bin/env python3
"""
Render the board as a dark-mode PNG for X.

    python3 xcard.py board.json --label "NFL · 1:00 wave" --out card.png
    python3 xcard.py board.json --open         # render to /tmp and open it

X demotes plain text and gives an image several times the reach of the same
content typed out, and 280 characters only ever fit a five-row headline. The
card carries the whole CV / SZ / WAGER / PLAY table instead.

THE SAME PRIVACY RULES AS xpost.py APPLY, and an image is harder to retract
than text, so they are enforced here rather than trusted to the caller:
  * wallets render as anon.py codenames, never a username or address
  * stake and hold are blurred through anon.money / anon.pct
  * "Nx normal size" prints only when the baseline is trustworthy — a $24
    median makes a routine bet read as 414x, and that number on a public image
    is the most embarrassing thing this project can publish
  * nothing from bets.jsonl is ever read here

Rendering is headless Chrome on an HTML string. launchd runs /usr/bin/python3,
which has no PIL, and Chrome sets type far better than PIL would anyway. Every
height below is a fixed pixel value so the window can be sized to the content
exactly — headless Chrome screenshots the window, it does not crop to the page.
"""
import html, json, os, subprocess, sys, tempfile, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import anon
import units as U
import bigboard as B

CHROME = os.environ.get("CHROME_BIN") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"   # server: CHROME_BIN=/usr/bin/chromium
HANDLE = "@tradesbotter"
MAX_ROWS = 8             # past this the card goes taller than X shows uncropped
MIN_CONV = 65

W = 1200                 # CSS px; rendered at 2x -> 2400 wide
H_HEAD, H_COLS, H_ROW, H_FOOT = 168, 44, 96, 124

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:%(W)dpx;background:#0a0d10;overflow:hidden}
body{font-family:-apple-system,'SF Pro Text','Helvetica Neue',sans-serif;
  color:#e9eef3;-webkit-font-smoothing:antialiased}
.num{font-family:'SF Mono',Menlo,monospace;font-variant-numeric:tabular-nums}
.head{height:%(H_HEAD)dpx;padding:40px 56px 0;position:relative;
  background:linear-gradient(180deg,#11161b 0%%,#0a0d10 100%%)}
.head:before{content:'';position:absolute;left:0;top:0;width:100%%;height:4px;
  background:linear-gradient(90deg,#f5b83d 0%%,#f5b83d 28%%,transparent 100%%)}
.kicker{font:700 15px/1 -apple-system,sans-serif;letter-spacing:.22em;
  color:#f5b83d;text-transform:uppercase}
.title{font:700 52px/1 'DIN Condensed','DIN Alternate','Arial Narrow',sans-serif;
  letter-spacing:.02em;text-transform:uppercase;margin-top:14px;white-space:nowrap}
.sub{font-size:17px;color:#8492a0;margin-top:8px}
.stamp{position:absolute;right:56px;top:44px;text-align:right;
  font-size:15px;color:#8492a0;line-height:1.5}
.stamp b{color:#e9eef3;font-weight:600}
.cols,.row{display:grid;align-items:center;padding:0 56px;
  grid-template-columns:96px 84px 1fr 120px 130px 250px;column-gap:20px}
.cols{height:%(H_COLS)dpx;font:600 12px/1 -apple-system,sans-serif;
  letter-spacing:.16em;color:#5d6a77;border-top:1px solid #1b222a;
  border-bottom:1px solid #1b222a;background:#0d1115}
.r{text-align:right}
.row{height:%(H_ROW)dpx;border-bottom:1px solid #151b21}
.row:nth-child(even){background:#0c1014}
.cv{font-size:34px;font-weight:600;line-height:1}
.bar{height:4px;width:72px;background:#1b222a;border-radius:2px;margin-top:9px}
.bar i{display:block;height:4px;border-radius:2px;background:#f5b83d}
.sz{display:inline-block;font-size:18px;font-weight:600;padding:6px 10px;
  border:1px solid #2a343e;border-radius:6px;color:#e9eef3}
.sz.hi{border-color:#f5b83d;color:#f5b83d}
.sz.dim{border-color:#1b222a;color:#5d6a77;font-size:13px;letter-spacing:.08em}
.play{font-size:27px;font-weight:650;letter-spacing:-.01em;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.game{font-size:15px;color:#8492a0;margin-top:6px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.game b{color:#b9c4cf;font-weight:600;letter-spacing:.06em;font-size:13px;
  margin-right:8px}
.odds{font-size:26px;font-weight:600}
.wager{font-size:22px;color:#e9eef3}
.who{font-size:17px;font-weight:600;color:#e9eef3;white-space:nowrap}
.stats{font-size:14px;color:#8492a0;margin-top:6px;white-space:nowrap}
.stats em{font-style:normal;color:#f5b83d}
.foot{height:%(H_FOOT)dpx;padding:0 56px;display:flex;align-items:center;
  justify-content:space-between;font-size:14px;color:#5d6a77;line-height:1.5}
.foot b{color:#e9eef3;font-weight:600;font-size:17px}
.foot>div:first-child{max-width:900px}
.foot .key{color:#8492a0}.foot .key b{font-size:14px;color:#f5b83d;letter-spacing:.08em}
.foot .more{color:#f5b83d;font-weight:600}
""" % dict(W=W, H_HEAD=H_HEAD, H_COLS=H_COLS, H_ROW=H_ROW, H_FOOT=H_FOOT)


def _start(h):
    s = h.get("start")
    if isinstance(s, str):
        try:
            s = datetime.fromisoformat(s)
        except ValueError:
            return ""
    if not s:
        return ""
    now = datetime.now(s.tzinfo)
    if s <= now:
        return "LIVE"
    loc = s.astimezone()
    day = "" if loc.date() == now.astimezone().date() else f"{loc:%a} "
    return f"{day}{loc:%-I:%M %p}"


def _trusted(h):
    return ((h.get("n_open") or 0) >= U.TRUST_POSITIONS
            and (h.get("typical") or 0) >= U.TRUST_TYPICAL)


def _units(h):
    """Recommended size — but a play already published as a pick keeps the
    size it was published at (picks.locked_units), so the board never
    contradicts the pick post. Imported lazily: picks imports postplays."""
    u = U.recommend(h)[0]
    try:
        import picks
        return picks.locked_units(h, u)
    except Exception:
        return u


def _row(h):
    e = html.escape
    u, size_label = U.public_size(h)
    conv = h.get("conv") or 0
    stats = [f"{anon.pct(h.get('hold'))} hold"]
    vt = h.get("vs_typical")
    if vt and vt >= 2 and _trusted(h):
        stats.append(f"<em>{vt:.0f}x their normal size</em>")
    game = B.game_text(h)
    lg, _, matchup = game.partition(" ")
    start = _start(h)
    return (
        '<div class="row">'
        f'<div><div class="cv num">{conv:.0f}</div>'
        f'<div class="bar"><i style="width:{max(0, min(100, conv)):.0f}%"></i></div></div>'
        f'<div><span class="sz num{" hi" if u >= 3 else ""}{" dim" if not u else ""}">{e(size_label)}</span></div>'
        f'<div style="min-width:0"><div class="play">{e(B.play_text(h))}</div>'
        f'<div class="game"><b>{e(lg)}</b>{e(matchup)}'
        f'{" · " + e(start) if start else ""}</div></div>'
        f'<div class="odds num r">{e(B.chk.am(h.get("entry") or 0))}</div>'
        f'<div class="wager num r">{e(anon.money(h.get("stake")))}</div>'
        # keyed on `user`, exactly as xpost.pick_post does — a different key
        # would give the same trader a second codename on the pick posts
        f'<div><div class="who">{e(anon.alias(h.get("user")))}</div>'
        f'<div class="stats">{" · ".join(stats)}</div></div>'
        '</div>')


def build(rows, label):
    """(html, height_px, n_shown). Rows are filtered and ordered here so a
    caller cannot put a sub-60 row on a public image by passing a raw board."""
    rows = [r for r in rows if (r.get("conv") or 0) >= MIN_CONV]
    rows.sort(key=lambda r: -(r.get("conv") or 0))
    shown, extra = rows[:MAX_ROWS], max(0, len(rows) - MAX_ROWS)
    if not shown:
        return None, 0, 0
    now = datetime.now().astimezone()
    tracked = sum(r.get("stake") or 0 for r in rows)
    more = f'<span class="more">+{extra} more on the board</span> · ' if extra else ""
    page = (
        f'<!doctype html><meta charset="utf-8"><style>{CSS}</style>'
        '<div class="head"><div class="kicker">Sharp money board</div>'
        f'<div class="title">{html.escape(label)}</div>'
        '<div class="sub">What tracked sharp wallets are holding right now. '
        'CONV rates the bettor; only −122 to −180 favourites are sized.</div>'
        f'<div class="stamp"><b>{now:%a %b %-d · %-I:%M %p} {now:%Z}</b><br>'
        f'{len(rows)} plays · {anon.money(tracked)} tracked</div></div>'
        '<div class="cols"><div>CONV</div><div>SIZE</div><div>PLAY</div>'
        '<div class="r">ENTRY</div><div class="r">WAGER</div><div>TRADER</div></div>'
        f'<div>{"".join(_row(h) for h in shown)}</div>'
        f'<div class="foot"><div><span class="key"><b>CONV</b> (0–100) rates the '
        'trader, not the odds: career hold, the size of this wager, and whether '
        'the price has moved since they bet. Picks are filtered to −122 … −180 '
        'separately. Not a win probability. <b>SIZE</b> is the stake in units.</span><br>'
        f'{more}Informational only — what anonymous traders hold. Not advice. '
        'Unit sizing is still in testing. Every call is settled publicly, win or lose.</div>'
        f'<div><b>{HANDLE}</b></div></div>')
    return page, H_HEAD + H_COLS + H_ROW * len(shown) + H_FOOT, len(shown)


def _shoot(page, height, out):
    """HTML string -> PNG at `out`. Returns (ok, detail)."""
    if not os.path.exists(CHROME):
        return False, f"Chrome not found at {CHROME}"
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "card.html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(page)
        if os.path.exists(out):
            os.remove(out)                # a stale card must never pass as new
        # its own profile dir, so this never collides with a Chrome the user
        # has open — sharing the default profile makes headless exit silently
        p = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--no-first-run", "--no-default-browser-check",
             f"--user-data-dir={os.path.join(td, 'profile')}",
             "--force-device-scale-factor=2", f"--window-size={W},{height}",
             f"--screenshot={out}", "file://" + src],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # New-headless Chrome writes the screenshot and then frequently never
        # exits, so waiting on the process hangs a launchd slot for good. Wait
        # on the FILE instead — present and the same size twice running — and
        # take Chrome down ourselves.
        last, deadline = -1, time.time() + 45
        while time.time() < deadline and p.poll() is None:
            time.sleep(0.4)
            size = os.path.getsize(out) if os.path.exists(out) else 0
            if size and size == last:
                break
            last = size
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=10)
    if not os.path.exists(out) or os.path.getsize(out) < 10_000:
        return False, "Chrome wrote no image"
    return True, f"{W*2}x{height*2}, {os.path.getsize(out)//1024} KB"


def render(rows, label, out):
    """Board card. Write the PNG to `out`. Returns (ok, detail)."""
    page, height, n = build(rows, label)
    if not page:
        return False, "nothing qualifies"
    ok, detail = _shoot(page, height, out)
    return ok, (f"{n} row(s), {detail}" if ok else detail)


# ------------------------------------------------------------- single pick
H_PICK = 520             # wider than 16:9 on purpose — 675 left a dead band mid-card

PICK_CSS = """
.pk{height:%dpx;padding:44px 64px 0;position:relative;
  background:radial-gradient(900px 500px at 85%% 0%%,#151b21 0%%,#0a0d10 70%%)}
.pk:before{content:'';position:absolute;left:0;top:0;width:100%%;height:4px;
  background:linear-gradient(90deg,#f5b83d 0%%,#f5b83d 28%%,transparent 100%%)}
.pk .lg{font:700 15px/1 -apple-system,sans-serif;letter-spacing:.22em;color:#f5b83d}
.pk .matchup{font-size:22px;color:#8492a0;margin-top:14px}
.pk .thepick{font:700 104px/1 'DIN Condensed','DIN Alternate','Arial Narrow',sans-serif;
  text-transform:uppercase;letter-spacing:.01em;margin-top:36px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.pk .price{display:flex;align-items:baseline;gap:22px;margin-top:14px}
.pk .odds{font-size:54px;font-weight:600}
.pk .book{font-size:22px;color:#8492a0}
.pk .u{position:absolute;right:64px;top:44px;text-align:center;
  border:2px solid #f5b83d;border-radius:14px;padding:16px 26px 14px}
.pk .u b{display:block;font-size:50px;font-weight:600;color:#f5b83d;line-height:1}
.pk .u span{font:600 12px/1 -apple-system,sans-serif;letter-spacing:.18em;color:#8492a0;
  display:block;margin-top:10px}
.pk .why{position:absolute;left:64px;right:64px;bottom:78px;display:flex;gap:56px;
  border-top:1px solid #1b222a;padding-top:22px}
.pk .why div{font-size:14px;color:#5d6a77;letter-spacing:.14em;font-weight:600}
.pk .why b{display:block;font-size:26px;color:#e9eef3;font-weight:600;
  letter-spacing:0;margin-top:10px;white-space:nowrap}
.pk .why b.hot{color:#f5b83d}
.pk .ft{position:absolute;left:64px;right:64px;bottom:24px;display:flex;
  justify-content:space-between;font-size:14px;color:#5d6a77}
.pk .ft b{color:#e9eef3;font-size:17px;font-weight:600}
""" % H_PICK


def _when(iso):
    try:
        loc = datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return ""
    return f"{loc:%a %-I:%M %p} {loc:%Z}"


def build_pick(p, alert=False):
    """One row of picks.jsonl -> HTML. Everything here is already public-safe
    in the ledger (alias, not wallet) except stake and hold, blurred below.

    alert=True is the same card for a play that is NOT a graded pick — no book
    price, a period market, or below the pick gate. It carries no unit size and
    says so, because a card with a SIZE box on it reads as a recommendation."""
    e = html.escape
    odds = p.get("odds") or 0
    big = ' style="font-size:64px;margin-top:48px"' if len(p.get("pick") or "") > 22 else ""
    why = [("TRADER", e(p.get("alias") or "Unknown"), ""),
           ("CAREER HOLD", e(anon.pct(p.get("hold"))), ""),
           ("THEIR WAGER", e(anon.money(p.get("stake"))), "")]
    vt = p.get("vs_typical")
    if vt and vt >= 2 and p.get("size_trusted"):
        why.append(("VS THEIR NORMAL", f"{vt:.0f}x size", "hot"))
    return (
        f'<!doctype html><meta charset="utf-8"><style>{CSS}{PICK_CSS}</style>'
        '<div class="pk">'
        f'<div class="lg">{e(p.get("league") or "")} · {"SHARP MONEY ALERT" if alert else "SHARP MONEY PLAY"}</div>'
        f'<div class="matchup">{e(p.get("title") or p.get("game") or "")}'
        f'{" · " + e(_when(p.get("start"))) if p.get("start") else ""}</div>'
        + (f'<div class="u"><b style="font-size:22px;line-height:1.15">NOT<br>GRADED</b><span>NO SIZE</span></div>' if alert else
           f'<div class="u num"><b>{e(U.fmt(p.get("units")))}</b><span>SIZE</span></div>')
        + f'<div class="thepick"{big}>{e(p.get("pick") or "")}</div>'
        + (f'<div class="price"><span class="odds num">{e(B.chk.am(p.get("sharp_entry") or 0))}</span>'
           f'<span class="book">their entry on Polymarket · {e(p.get("why_alert") or "no book price found")}</span></div>' if alert else
           f'<div class="price"><span class="odds num">{odds:+d}</span>'
           f'<span class="book">best price · {e(str(p.get("book") or ""))}</span></div>')
        + '<div class="why">'
        + "".join(f'<div>{k}<b class="{c}">{v}</b></div>' for k, v, c in why) +
        '</div><div class="ft"><div>Informational only — what an anonymous trader '
        'holds. Not advice. Unit sizing is still in testing.</div>'
        f'<div><b>{HANDLE}</b></div></div></div>')


def render_pick(p, out):
    if not p.get("odds") or not p.get("pick"):
        return False, "pick has no price — no card"
    return _shoot(build_pick(p), H_PICK, out)


def render_alert(p, out):
    if not p.get("pick"):
        return False, "no play text — no card"
    return _shoot(build_pick(p, alert=True), H_PICK, out)


def when_text(iso):
    """'Tue 1:05 PM EDT' — public helper so every post names the game time.
    A doubleheader (Rays/Yankees twice on 2026-09-22) is ambiguous without it."""
    return _when(iso)


# ---------------------------------------------------------- nightly results
R_HEAD, R_ROW, R_FOOT, R_MAX = 300, 60, 84, 10

RES_CSS = """
.rs .top{height:%(h)dpx;padding:44px 64px 0;position:relative;
  background:linear-gradient(180deg,#11161b 0%%,#0a0d10 100%%)}
.rs .top:before{content:'';position:absolute;left:0;top:0;width:100%%;height:4px;
  background:linear-gradient(90deg,#f5b83d 0%%,#f5b83d 28%%,transparent 100%%)}
.rs .lg{font:700 15px/1 -apple-system,sans-serif;letter-spacing:.22em;color:#f5b83d}
.rs .day{font-size:20px;color:#8492a0;margin-top:12px}
.rs .big{display:flex;gap:64px;margin-top:34px;align-items:flex-end}
.rs .big div{font:600 13px/1 -apple-system,sans-serif;letter-spacing:.16em;color:#5d6a77}
.rs .big b{display:block;font:700 92px/1 'DIN Condensed','DIN Alternate',sans-serif;
  color:#e9eef3;margin-bottom:12px;letter-spacing:.01em}
.rs .big b.sm{font-size:60px}
.up{color:#3ecf8e!important}.dn{color:#f0616d!important}
.rs .all{position:absolute;right:64px;top:44px;text-align:right;font-size:15px;
  color:#8492a0;line-height:1.6}
.rs .all b{color:#e9eef3;font-weight:600}
.rr{height:%(r)dpx;display:grid;align-items:center;padding:0 64px;column-gap:20px;
  grid-template-columns:44px 1fr 110px 90px 120px;border-top:1px solid #151b21;
  font-size:21px}
.rr:nth-child(even){background:#0c1014}
.rr .mk{font-weight:700;font-size:15px;letter-spacing:.08em}
.rr .pk2{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rr .pk2 span{color:#8492a0;font-weight:400;font-size:16px;margin-left:12px}
.rs .ft{height:%(f)dpx;padding:0 64px;display:flex;align-items:center;
  justify-content:space-between;font-size:14px;color:#5d6a77;
  border-top:1px solid #1b222a}
.rs .ft b{color:#e9eef3;font-size:17px;font-weight:600}
.rs .ft .more{color:#f5b83d;font-weight:600}
""" % dict(h=R_HEAD, r=R_ROW, f=R_FOOT)


def build_results(day_rows, all_rows, day, pl):
    """`pl` is picks.pl, passed in so the card can never value a pick
    differently from the text report. Returns (html, height) or (None, 0) —
    and None whenever any settled pick is unpriceable, the same refusal
    picks.report makes: a partial figure is not published."""
    e = html.escape
    rows = [p for p in day_rows if p.get("result")]
    vals = [pl(p) for p in rows]
    if not rows or any(v is None for v in vals):
        return None, 0
    w = sum(1 for p in rows if p["result"] == "won")
    staked, net = sum(p["units"] for p in rows), sum(vals)
    cls = "up" if net > 0 else "dn" if net < 0 else ""
    # record to date — only priceable picks, and said so by omission of none:
    # an unpriceable historic win is skipped from units but still counts W-L
    done = [p for p in all_rows if p.get("result")]
    aw = sum(1 for p in done if p["result"] == "won")
    anet = sum(v for v in (pl(p) for p in done) if v is not None)
    try:
        nice = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %-d")
    except ValueError:
        nice = day
    clv_html = ""
    try:
        import pickclv
        cs = pickclv.summary(all_rows)
        if cs:
            clv_html = f'<br>CLV vs book <b>{cs["mean"]:+.1f} pts</b> · beat close <b>{cs["beat"]:.0%}</b> ({cs["n"]})'
            if cs.get("entry_n"):
                clv_html += f'<br>signal CLV <b>{cs["entry_mean"]:+.1f} pts</b> ({cs["entry_n"]})'
    except Exception:
        pass
    order = sorted(zip(rows, vals), key=lambda t: -abs(t[1]))
    shown, extra = order[:R_MAX], max(0, len(order) - R_MAX)
    body = "".join(
        f'<div class="rr"><div class="mk {"up" if p["result"] == "won" else "dn"}">'
        f'{"W" if p["result"] == "won" else "L"}</div>'
        f'<div class="pk2">{e(p.get("pick") or "")}'
        f'<span>{e(p.get("league") or "")} · {e(p.get("game") or "")}</span></div>'
        f'<div class="num r">{("%+d" % p["odds"]) if p.get("odds") else "—"}</div>'
        f'<div class="num r">{e(U.fmt(p.get("units")))}</div>'
        f'<div class="num r {"up" if v > 0 else "dn"}">{v:+.2f}u</div></div>'
        for p, v in shown)
    more = f'<span class="more">+{extra} more</span> · ' if extra else ""
    page = (
        f'<!doctype html><meta charset="utf-8"><style>{CSS}{RES_CSS}</style>'
        '<div class="rs"><div class="top"><div class="lg">NIGHTLY RESULTS</div>'
        f'<div class="day">{e(nice)}</div>'
        f'<div class="all">Record to date<br><b>{aw}-{len(done)-aw}</b> · '
        f'<b>{anet:+.1f}u</b>{clv_html}</div>'
        '<div class="big">'
        f'<div><b>{w}-{len(rows)-w}</b>RECORD</div>'
        f'<div><b class="{cls}">{net:+.2f}u</b>NET</div>'
        f'<div><b class="sm">{staked:g}u</b>STAKED</div>'
        f'<div><b class="sm {cls}">{e(anon.roi(net/staked*100)) if staked else "—"}</b>ROI</div>'
        f'</div></div><div>{body}</div>'
        f'<div class="ft"><div>{more}Every play was posted with a price and a size '
        'before the game. Losing days get the same card.</div>'
        f'<div><b>{HANDLE}</b></div></div></div>')
    return page, R_HEAD + R_ROW * len(shown) + R_FOOT


def render_results(day_rows, all_rows, day, pl, out):
    page, height = build_results(day_rows, all_rows, day, pl)
    if not page:
        return False, "nothing settled, or an unpriced pick — no card"
    return _shoot(page, height, out)


def main():
    a = sys.argv[1:]
    files = [x for i, x in enumerate(a) if not x.startswith("--")
             and (i == 0 or a[i - 1] not in ("--label", "--out"))]
    if not files:
        print(__doc__.strip().split("\n\n")[1])
        return 1
    label = a[a.index("--label") + 1] if "--label" in a else "Big Board"
    out = a[a.index("--out") + 1] if "--out" in a else "/tmp/xcard.png"
    try:
        rows = json.load(open(files[0]))
    except (FileNotFoundError, ValueError) as e:
        print(f"could not read {files[0]}: {e}", file=sys.stderr)
        return 1
    ok, detail = render(rows, label, out)
    print(f"{'wrote ' + out if ok else 'FAILED'} — {detail}")
    if ok and "--open" in a:
        subprocess.run(["open", out])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
