#!/usr/bin/env python3
"""
Fire the all-sports X board 15 minutes before the day's first MLB game.

    python3 xboard-mlb.py            # what launchd runs, every 5 minutes
    python3 xboard-mlb.py --when     # print today's plan, fire nothing

ONLY ON NON-NFL WEEKDAYS (Tue / Wed / Fri). Monday and Thursday have fixed 8pm
boards ahead of MNF / TNF, and Saturday / Sunday run on the kickoff-wave
windows; those are plain StartCalendarInterval jobs. First pitch moves every
day — 1:05 one afternoon, 6:35 the next — so it cannot be a calendar entry, and
launchd has no "15 minutes before X". This polls instead: cheap, and unlike a
process that sleeps until the target it survives the Mac sleeping or rebooting.

The schedule comes from MLB's own public stats API, not from the Polymarket
slate: it is the authority on start times, costs no sweep, and knows about
postponements. It is fetched once a day and cached in the state file.

NO MLB THAT DAY (off-season, or the fetch failed all day): falls back to 8pm, so
a Tuesday in December still gets a board for whatever is on.

The board is sent once per day. If the window was missed — machine off at T-15 —
it still fires up to LATE_MIN after the target, because `--pre` drops started
games on its own and the rest of the slate is still to come. Later than that it
is skipped and logged rather than posted into the middle of the evening.
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "xboard-mlb-state.json")
LOG = os.path.join(HERE, "xboard.log")
DAYS = {1, 2, 4}            # Mon=0: Tue, Wed, Fri
LEAD_MIN = 15
LATE_MIN = 45
FALLBACK_HOUR = 20
API = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="
DEAD = ("Postponed", "Cancelled", "Suspended")


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"[{datetime.now().astimezone():%F %T %Z}] mlb-sched | {msg}\n")


def first_pitch(day):
    """Earliest live start that falls on local `day`, or None. Raises on a
    failed fetch so the caller can tell 'no games' from 'could not ask'."""
    p = subprocess.run(["curl", "-s", "-f", "--max-time", "20", API + day.isoformat()],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"curl exit {p.returncode}")
    starts = []
    for d in json.loads(p.stdout).get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("detailedState", "").startswith(DEAD):
                continue
            t = datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00")).astimezone()
            if t.date() == day:
                starts.append(t)
    return min(starts) if starts else None


def plan(now):
    """Today's target send time, cached. {'day','target','why','sent'}."""
    day = now.date()
    try:
        st = json.load(open(STATE))
    except (FileNotFoundError, ValueError):
        st = {}
    if st.get("day") == day.isoformat() and st.get("target"):
        return st
    st = {"day": day.isoformat(), "sent": False}
    fallback = now.replace(hour=FALLBACK_HOUR, minute=0, second=0, microsecond=0)
    try:
        fp = first_pitch(day)
    except Exception as e:
        # do NOT cache: ask again in five minutes. Only past 7pm give up and
        # commit to the 8pm fallback so the day still gets a board.
        if now.hour < FALLBACK_HOUR - 1:
            log(f"schedule fetch failed ({e}) — will retry")
            return None
        fp, why = None, f"schedule fetch failed ({e}) — 8pm fallback"
    else:
        why = "no MLB today — 8pm fallback"
    if fp:
        st["target"] = (fp - timedelta(minutes=LEAD_MIN)).isoformat()
        st["why"] = f"first pitch {fp:%-I:%M %p}"
    else:
        st["target"], st["why"] = fallback.isoformat(), why
    save(st)
    log(f"plan: send {datetime.fromisoformat(st['target']):%-I:%M %p} ({st['why']})")
    return st


def save(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def main():
    now = datetime.now().astimezone()
    if "--when" in sys.argv:
        if now.weekday() not in DAYS:
            print(f"{now:%A}: not a Tue/Wed/Fri — fixed-time jobs cover today")
            return 0
        st = plan(now)
        print(st and f"{st['day']}: send at "
              f"{datetime.fromisoformat(st['target']):%-I:%M %p} — {st['why']}"
              f"{' — ALREADY SENT' if st.get('sent') else ''}")
        return 0
    if now.weekday() not in DAYS:
        return 0
    st = plan(now)
    if not st or st.get("sent"):
        return 0
    target = datetime.fromisoformat(st["target"])
    if now < target:
        return 0
    st["sent"] = True           # before the run: a slow sweep must not double-fire
    save(st)
    if now > target + timedelta(minutes=LATE_MIN):
        log(f"missed the {target:%-I:%M %p} window by more than {LATE_MIN}m — skipped")
        return 0
    subprocess.run([os.path.join(HERE, "run-xboard.sh"), "all",
                    f"BIG BOARD · {now:%A}"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
