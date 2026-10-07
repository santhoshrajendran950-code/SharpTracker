#!/usr/bin/env python3
"""
Push-only Discord delivery. One HTTPS POST per message, no bot, no gateway.

    python3 discordsink.py --outbox                     # windows-outbox.json -> #big-board
    python3 discordsink.py --channel sharp alert-body.txt
    check.py --alert 60 | python3 discordsink.py --channel sharp --title "Sharp Alert"
    python3 discordsink.py --check                      # verify config, post nothing
    python3 discordsink.py --outbox --dry               # print what would go out

A second adapter beside deploy/mailer.py, not a replacement. Delivery stays
decoupled from detection: windows.py writes windows-outbox.json and knows
nothing about where it goes, exactly as it knows nothing about email.

NOT named discord.py on purpose — that shadows the real `discord` package, and
any future `import discord` in this directory would silently import this file.

Webhook URLs resolve the same way oddskey.py resolves the odds key:
  1. env  DISCORD_WEBHOOK_SHARP / DISCORD_WEBHOOK_BOARD
  2. macOS Keychain, service "nightly-line-discord", account "sharp" / "board"
  3. ~/.nightly-line/.discord-<channel>, chmod 600   (launchd may not reach the
     keychain, which is why oddskey.py keeps a file fallback too)
A webhook URL IS the credential — anyone holding it can post to the channel — so
it is never printed, never logged, and redacted in every error path.

THREE THINGS THIS GUARDS, all of which are why a channel is riskier than email:

  bets.jsonl can never reach Discord. A channel is visible to everyone in the
  server, persistent and searchable — a weaker boundary than a BCC'd email. The
  guard is structural, not a convention: any payload carrying bet-log markers, or
  sourced from the private ledgers/ directory, is refused outright. dayledger.py
  is the only script that reads that file and it has no path to this one.

  PARTIAL warnings survive chunking. "!! N wallet lookups failed" is repeated on
  EVERY chunk, outside the code fence, because in a channel a partial scan and a
  quiet slate look identical — the failure mode that has already fooled this
  system three times. A reader scrolling to the third message must still see it.

  The 2000-character limit. Messages are split on line boundaries and fenced, so
  fixed-width alignment survives. A split never lands mid-table-row.
"""
import json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIMIT = 2000
SAFE = 1880                 # leaves room for the fence and the partial banner
SERVICE = "nightly-line-discord"
# Base channels, plus one per sport. A sport channel FALLS BACK to its base
# channel when no webhook is stored for it, so per-sport routing can be switched
# on one channel at a time without anything going dark in between.
# Three sports get their own channel; everything else lands in one misc feed.
# Splitting soccer and tennis out too would be eight webhooks to create and
# rotate for leagues Santhosh has not bet, so they start pooled and can be
# promoted later by storing a webhook under their own name.
SPORTS = ["nfl", "cfb", "mlb", "misc"]
CHANNELS = {"sharp": "DISCORD_WEBHOOK_SHARP", "board": "DISCORD_WEBHOOK_BOARD"}
for _s in SPORTS:
    CHANNELS[f"sharp-{_s}"] = f"DISCORD_WEBHOOK_SHARP_{_s.upper()}"
    CHANNELS[f"board-{_s}"] = f"DISCORD_WEBHOOK_BOARD_{_s.upper()}"

# league label on a board row -> channel suffix
LEAGUE_CHANNEL = {"NFL": "nfl", "NCAAF": "cfb", "MLB": "mlb"}


def channel_for(base, league):
    """#sharp-nfl / #sharp-cfb / #sharp-mlb, everything else #sharp-misc."""
    return f"{base}-{LEAGUE_CHANNEL.get((league or '').upper(), 'misc')}"


def base_of(channel):
    return channel.split("-", 1)[0]

# Any of these in a payload means the bet log leaked into it. Refuse, loudly.
BET_MARKERS = ("bets.jsonl", "MY BETS", "MY POSITIONS", "my stake", "P/L (mine)",
               "PERSONAL P/L", "BET LOG")
PRIVATE_DIRS = ("ledgers",)


def redact(s):
    """Never let a webhook URL reach a log or a traceback."""
    out = []
    for tok in str(s).split():
        out.append("https://discord.com/api/webhooks/<redacted>"
                   if "discord.com/api/webhooks" in tok or "discordapp.com/api/webhooks" in tok
                   else tok)
    return " ".join(out)


def webhook(channel, allow_fallback=True):
    env = CHANNELS.get(channel)
    if not env:
        raise RuntimeError(f"unknown channel {channel!r}; expected one of {list(CHANNELS)}")
    v = os.environ.get(env)
    if v:
        return v.strip()
    try:
        r = subprocess.run(["security", "find-generic-password",
                            "-s", SERVICE, "-a", channel, "-w"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    f = HERE / f".discord-{channel}"
    if f.exists():
        t = f.read_text().strip()
        if t:
            if (f.stat().st_mode & 0o077):
                print(f"!! {f} is group/world readable — chmod 600 it", file=sys.stderr)
            return t
    if allow_fallback and "-" in channel:
        # #sharp-nfl not configured yet -> fall back to #sharp-plays
        return webhook(base_of(channel), allow_fallback=False)
    raise RuntimeError(
        f"no webhook for {channel!r}. Set it with either:\n"
        f"  security add-generic-password -s {SERVICE} -a {channel} -w '<URL>'\n"
        f"  printf '%s' '<URL>' > {f} && chmod 600 {f}\n"
        f"  export {env}='<URL>'")


def looks_like_webhook(v):
    """Shape check. A '--check' that passes on the literal placeholder <URL> is
    worse than no check — it reports healthy right up until the first real send
    fails. Both keychain slots were once populated with the five characters
    '<URL>' from a copy-pasted example, and nothing caught it."""
    v = (v or "").strip()
    if v in ("<URL>", "'<URL>'", '"<URL>"') or len(v) < 60:
        return False, "placeholder or too short to be a webhook URL"
    if not v.startswith(("https://discord.com/api/webhooks/",
                         "https://discordapp.com/api/webhooks/",
                         "https://canary.discord.com/api/webhooks/")):
        return False, "does not start with https://discord.com/api/webhooks/"
    tail = v.rsplit("/api/webhooks/", 1)[-1].split("/")
    if len(tail) < 2 or not tail[0].isdigit() or len(tail[1]) < 20:
        return False, "missing the <id>/<token> tail"
    return True, None


def guard(text, source=None):
    """Structural refusal. The bet log must never be postable, by any path."""
    if source:
        parts = Path(source).resolve().parts
        for d in PRIVATE_DIRS:
            if d in parts:
                raise RuntimeError(
                    f"REFUSED: {source} is under the private {d}/ directory. "
                    f"That is the personal bet ledger and must never leave this machine.")
    low = text.lower()
    for m in BET_MARKERS:
        if m.lower() in low:
            raise RuntimeError(
                f"REFUSED: payload contains {m!r}, a bet-log marker. Nothing "
                f"derived from bets.jsonl may be posted to a channel.")


def partial_banner(text):
    """The '!! ... PARTIAL / FAILED' line, if the source carried one."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("!!") and ("PARTIAL" in s.upper() or "FAIL" in s.upper()):
            return s
        if "FAILED, PARTIAL" in s.upper():
            # the header carries it mid-line: "... · 1083/1084 wallets · !! 1 FAILED,
            # PARTIAL". Take the last segment and don't re-prefix an existing "!!".
            seg = s.split("·")[-1].strip().lstrip("!").strip()
            return "!! " + seg
    return None


def chunk(text, banner=None, fence=True):
    """Split under the 2000-char limit on line boundaries, fenced, banner on each."""
    head = (banner + "\n") if banner else ""
    open_f, close_f = ("```\n", "\n```") if fence else ("", "")
    budget = SAFE - len(head) - len(open_f) - len(close_f)
    out, cur = [], []
    n = 0
    for line in text.splitlines():
        while len(line) > budget:            # a single over-long line: hard split
            if cur:
                out.append("\n".join(cur))
                cur, n = [], 0
            out.append(line[:budget])
            line = line[budget:]
        if n + len(line) + 1 > budget and cur:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return [head + open_f + c + close_f for c in out] or [head + open_f + close_f]


def probe(url):
    """Is this webhook live? A GET returns its metadata and posts NOTHING.

    --check used to only validate the SHAPE of the stored URL, which says
    nothing about whether the webhook still exists — a deleted one looks
    perfectly well-formed. This is the difference between "the string looks
    right" and "the channel will receive messages", without putting a test
    message in front of anyone.
    """
    p = subprocess.run(["curl", "-s", "--max-time", "15", "-w", "\n%{http_code}", url],
                       capture_output=True, text=True)
    body, _, code = p.stdout.rpartition("\n")
    if code != "200":
        try:
            msg = json.loads(body).get("message", "")
        except Exception:
            msg = ""
        return False, f"HTTP {code}" + (f" — {msg}" if msg else "")
    try:
        d = json.loads(body)
        return True, f"#{d.get('channel_id','?')} \"{d.get('name','')}\""
    except Exception:
        return True, ""


def post(url, content, retries=4):
    """One webhook POST, with Discord's own 429 retry_after honoured."""
    payload = json.dumps({"content": content, "allowed_mentions": {"parse": []}})
    for a in range(retries + 1):
        p = subprocess.run(
            ["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}",
             "-H", "Content-Type: application/json", "-X", "POST",
             "--data-binary", "@-", url],
            input=payload, capture_output=True, text=True)
        body, _, code = p.stdout.rpartition("\n")
        if code in ("200", "204"):
            return True, None
        if code == "429":
            wait = 1.5 * (2 ** a)
            try:
                wait = float(json.loads(body).get("retry_after", wait))
            except Exception:
                pass
            time.sleep(min(wait, 30))
            continue
        if a >= retries:
            return False, f"HTTP {code}: {redact(body)[:200]}"
        time.sleep(1.0 * (2 ** a))
    return False, "retries exhausted"


def send(channel, title, text, source=None, dry=False):
    guard(text, source)
    banner = partial_banner(text)
    body = (f"**{title}**\n" if title else "") + ""
    parts = chunk(text, banner=banner)
    if title:
        parts[0] = f"**{title}**\n" + parts[0]
    if dry:
        for i, c in enumerate(parts, 1):
            print(f"--- would post to #{channel} ({i}/{len(parts)}, {len(c)} chars) ---")
            print(c)
        return len(parts), 0
    url = webhook(channel)
    ok = err = 0
    for i, c in enumerate(parts):
        good, why = post(url, c)
        if good:
            ok += 1
        else:
            err += 1
            print(f"!! chunk {i+1}/{len(parts)} to #{channel} FAILED: {why}", file=sys.stderr)
        time.sleep(0.4)                      # webhook buckets are ~5 per 2s
    return ok, err


def main():
    a = sys.argv[1:]
    dry = "--dry" in a
    if "--check" in a:
        bad = 0
        configured = []
        for c in CHANNELS:
            try:
                webhook(c, allow_fallback=False)
                configured.append(c)
            except RuntimeError:
                pass
        if not configured:
            configured = ["sharp", "board"]
        extra = [c for c in CHANNELS if c not in configured and "-" in c]
        for c in configured:
            try:
                v = webhook(c)
            except RuntimeError as e:
                bad += 1
                print(f"  #{c:14} NOT configured\n{redact(e)}")
                continue
            ok, why = looks_like_webhook(v)
            if ok:
                live, detail = probe(v)
                wid = v.rsplit('/api/webhooks/', 1)[-1].split('/')[0]
                if live:
                    print(f"  #{c:14} LIVE  (id {wid}) {detail}")
                else:
                    bad += 1
                    print(f"  #{c:14} DEAD  (id {wid}) — {detail}")
                    print(f"           the webhook was deleted in Discord; "
                          f"create a new one and store it with -U")
            else:
                bad += 1
                print(f"  #{c:14} INVALID — {why}")
                print(f"           overwrite it with -U:")
                print(f"           security add-generic-password -U -s {SERVICE} -a {c} -w")
        return 1 if bad else 0

    if "--selftest" in a:
        import time as _t
        bad = 0
        stamp = _t.strftime("%a %-I:%M:%S %p %Z")
        for c in CHANNELS:
            try:
                url = webhook(c)
            except RuntimeError as e:
                print(f"  #{c:6} NOT CONFIGURED")
                bad += 1
                continue
            ok, why = looks_like_webhook(url)
            if not ok:
                print(f"  #{c:14} INVALID — {why}")
                bad += 1
                continue
            good, err = post(url, f"`selftest {stamp}` — delivery OK")
            print(f"  #{c:6} " + ("DELIVERED" if good else f"FAILED — {err}"))
            bad += 0 if good else 1
        print("selftest: " + ("all channels delivering" if not bad
                              else f"{bad} channel(s) NOT delivering"))
        if extra:
            print(f"\n  {len(extra)} sport channel(s) not configured — those fall "
                  f"back to their base channel:")
            print("    " + ", ".join("#" + c for c in extra))
        return 1 if bad else 0

    channel = a[a.index("--channel") + 1] if "--channel" in a else "board"
    # --if-configured: a scheduled job should post when Discord is set up and
    # stay silent when it is not, rather than logging a failure every run.
    if "--if-configured" in a:
        # Silent ONLY when no webhook is set at all. A webhook that is set but
        # malformed is a misconfiguration, not an opt-out, and silence there is
        # how a channel quietly stops receiving anything for days.
        try:
            v = webhook(channel)
        except RuntimeError:
            return 0
        ok, why = looks_like_webhook(v)
        if not ok:
            print(f"!! #{channel} webhook is CONFIGURED BUT INVALID ({why}) — "
                  f"nothing sent. Fix with: security add-generic-password -U "
                  f"-s {SERVICE} -a {channel} -w", file=sys.stderr)
            return 2
    title = a[a.index("--title") + 1] if "--title" in a else None

    if "--outbox" in a:
        f = HERE / "windows-outbox.json"
        try:
            items = json.load(open(f))
        except (FileNotFoundError, ValueError):
            print(f"no queue at {f}")
            return 0
        if not items:
            return 0
        tot_ok = tot_err = 0
        for it in items:
            ok, err = send(channel, it.get("subject"), it.get("body") or "",
                           source=str(f), dry=dry)
            tot_ok += ok
            tot_err += err
        print(f"[{len(items)} message(s), {tot_ok} chunk(s) sent"
              + (f", {tot_err} FAILED" if tot_err else "") + f" -> #{channel}]")
        return 1 if tot_err else 0

    files = [x for x in a if not x.startswith("--") and x not in (channel, title or "")]
    if files:
        src = files[0]
        text = open(src).read()
    else:
        src, text = None, sys.stdin.read()
    if not text.strip():
        return 0
    ok, err = send(channel, title, text, source=src, dry=dry)
    print(f"[{ok} chunk(s) sent" + (f", {err} FAILED" if err else "") + f" -> #{channel}]")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
