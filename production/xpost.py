#!/usr/bin/env python3
"""
Compose (and, once credentialed, post) to X.

    python3 xpost.py --pick  plays.json   # pre-game, one play
    python3 xpost.py --daily              # end-of-day scoreboard
    python3 xpost.py --monthly            # month scoreboard
    python3 xpost.py --daily --send       # actually post it
    python3 xpost.py --board b.json --label NFL --image   # board + PNG card
    python3 xpost.py --check-media card.png   # test image upload, posts nothing

DRY RUN IS THE DEFAULT AND --send IS REQUIRED. A post to X is public,
permanent, indexed and screenshotted; unlike the Drive sheet it cannot be
revised or withdrawn in any meaningful sense. Nothing here fires on a schedule
until that is wired deliberately.

WHAT NEVER LEAVES THE MACHINE:
  * dollar stakes and bankroll — posts carry UNITS and ROI only
  * wallet identities — anon.py maps them to stable one-way codenames
  * precise figures — money and hold are deliberately blurred, because an exact
    stake plus an exact hold fingerprints one account out of a thousand

Posting needs OAuth 1.0a (four credentials) rather than a webhook, so unlike
Discord this cannot be a copy-paste setup. Credentials resolve the same way the
rest of the project resolves secrets: env first, then Keychain.
"""
import base64, hashlib, hmac, json, os, secrets, subprocess, sys, time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import anon
import units as U

BETS = os.path.join(HERE, "bets.jsonl")
SERVICE = "nightly-line-x"
CREDS = ["api_key", "api_secret", "access_token", "access_secret"]
API = "https://api.x.com/2/tweets"
MEDIA_API = "https://api.x.com/2/media/upload"


# ---------------------------------------------------------------- credentials
def cred(name):
    v = os.environ.get(f"X_{name.upper()}")
    if v:
        return v.strip()
    r = subprocess.run(["security", "find-generic-password",
                        "-s", SERVICE, "-a", name, "-w"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    raise RuntimeError(
        f"no X credential {name!r}. Store all four with:\n"
        + "\n".join(f"  security add-generic-password -U -s {SERVICE} -a {c} -w"
                    for c in CREDS))


def _quiet(name):
    try:
        return cred(name)
    except RuntimeError:
        return None


def have_creds():
    try:
        for c in CREDS:
            cred(c)
        return True
    except RuntimeError:
        return False


# ------------------------------------------------------------- OAuth 1.0a
def _sign(method, url, params, api_secret, token_secret):
    base = "&".join([method.upper(),
                     urllib.parse.quote(url, safe=""),
                     urllib.parse.quote("&".join(
                         f"{urllib.parse.quote(k, safe='')}="
                         f"{urllib.parse.quote(str(v), safe='')}"
                         for k, v in sorted(params.items())), safe="")])
    key = f"{urllib.parse.quote(api_secret, safe='')}&" \
          f"{urllib.parse.quote(token_secret, safe='')}"
    return base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()


def _auth(method, url):
    """OAuth 1.0a Authorization header. Only the oauth_* params are signed —
    true for a JSON body and for multipart alike, which is every call here."""
    oauth = {
        "oauth_consumer_key": cred("api_key"),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": cred("access_token"),
        "oauth_version": "1.0",
    }
    oauth["oauth_signature"] = _sign(method, url, oauth,
                                     cred("api_secret"), cred("access_secret"))
    return "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth.items()))


def upload_media(path):
    """POST /2/media/upload, one-shot multipart. Returns (media_id, detail).

    An uploaded image is NOT public — it is invisible until a post attaches it
    and expires on its own if none does, so this is safe to call in a dry run.
    """
    p = subprocess.run(
        ["curl", "-s", "--max-time", "60", "-w", "\n%{http_code}",
         "-H", f"Authorization: {_auth('POST', MEDIA_API)}",
         "-F", f"media=@{path};type=image/png",
         "-F", "media_category=tweet_image", "-F", "media_type=image/png",
         MEDIA_API],
        capture_output=True, text=True)
    body, _, code = p.stdout.rpartition("\n")
    if code not in ("200", "201"):
        return None, f"HTTP {code}: {body[:200]}"
    try:
        d = json.loads(body)
        mid = (d.get("data") or {}).get("id") or d.get("media_id_string")
    except ValueError:
        mid = None
    return (str(mid), "ok") if mid else (None, f"no media id in: {body[:200]}")


def verify():
    """GET /2/users/me — proves the four credentials sign correctly and shows
    which handle they post as. Read-only, so it does NOT prove write permission;
    that only fails at the first real post, which is why the app permission step
    below matters more than it looks."""
    url = "https://api.x.com/2/users/me"
    oauth = {
        "oauth_consumer_key": cred("api_key"),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": cred("access_token"),
        "oauth_version": "1.0",
    }
    oauth["oauth_signature"] = _sign("GET", url, oauth,
                                     cred("api_secret"), cred("access_secret"))
    header = "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth.items()))
    p = subprocess.run(["curl", "-s", "--max-time", "20", "-w", "\n%{http_code}",
                        "-H", f"Authorization: {header}", url],
                       capture_output=True, text=True)
    body, _, code = p.stdout.rpartition("\n")
    if code != "200":
        return False, f"HTTP {code}: {body[:200]}"
    try:
        d = json.loads(body).get("data", {})
        return True, f"@{d.get('username','?')} ({d.get('name','')})"
    except ValueError:
        return True, ""


def post(text, media_id=None):
    """POST /2/tweets. Body is JSON, so only the oauth_* params are signed."""
    oauth = {
        "oauth_consumer_key": cred("api_key"),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": cred("access_token"),
        "oauth_version": "1.0",
    }
    oauth["oauth_signature"] = _sign("POST", API, oauth,
                                     cred("api_secret"), cred("access_secret"))
    header = "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth.items()))
    payload = {"text": text}
    if media_id:
        payload["media"] = {"media_ids": [media_id]}
    p = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}",
         "-H", f"Authorization: {header}", "-H", "Content-Type: application/json",
         "-X", "POST", "--data-binary", "@-", API],
        input=json.dumps(payload), capture_output=True, text=True)
    body, _, code = p.stdout.rpartition("\n")
    return code in ("200", "201"), f"HTTP {code}: {body[:200]}"



def post_with_image(text, image, text_if_no_image=None):
    """Post `text` with the PNG at `image` attached. (ok, detail, had_image).

    `text_if_no_image` is what goes out instead when the image does not make
    it: a short caption is only enough while the card is there to carry the
    detail, so a failed upload must fall back to the full text, not the caption.

    The image is an enhancement, never a gate: if it is missing or the upload
    fails, the text still goes out alone — a pick posted late because a card
    would not render is worse than a pick posted plain. The file is removed
    either way."""
    media_id = None
    if image and os.path.exists(image):
        media_id, detail = upload_media(image)
        if not media_id:
            print(f"image upload failed ({detail}) — posting text only")
        try:
            os.remove(image)
        except OSError:
            pass
    ok, why = post(text if media_id or not text_if_no_image else text_if_no_image,
                   media_id)
    return ok, why, bool(media_id)

# ------------------------------------------------------------------ composers
def ret(b):
    return b["to_win"] if b["result"] == "won" else -b["risk"]


def load(day=None, month=None):
    rows = [json.loads(l) for l in open(BETS) if l.strip()]
    rows = [b for b in rows if b.get("result") in ("won", "lost")]
    if day:
        rows = [b for b in rows if b.get("date") == day]
    if month:
        rows = [b for b in rows if (b.get("date") or "").startswith(month)]
    return rows


def scoreboard(rows, label, unit_usd=None):
    """Units and ROI only — never dollars."""
    if not rows:
        return None
    w = sum(1 for b in rows if b["result"] == "won")
    risked = sum(b["risk"] for b in rows)
    pl = sum(ret(b) for b in rows)
    base = unit_usd or U.UNIT_USD
    games = len({(b["date"], b.get("game")) for b in rows})
    out = [f"{label}  {w}-{len(rows)-w}",
           f"risked {risked/base:.1f}u · P/L {pl/base:+.1f}u",
           f"ROI {anon.roi(pl/risked*100)}"]
    if len(rows) / max(games, 1) >= 1.5:
        out.append(f"({len(rows)} bets over {games} games — treat as {games} outcomes)")
    return "\n".join(out)


def board_post(rows, label, max_rows=5):
    """Compact board roundup for X. 280 characters is the binding constraint —
    a Saturday 70+ board runs to 19 rows and several thousand characters, so
    this is a headline, not the board. Wallet aliases are omitted entirely:
    there is no room, and the individual pick posts already carry them.
    """
    import postplays as P
    rows = sorted(rows, key=lambda r: -(r.get("conv") or 0))
    shown, out = [], [label, ""]
    for h in rows[:max_rows]:
        u, label = U.public_size(h)          # units inside the pick window, else 'alert'
        if label == "—":
            continue
        side = P.play_text(h)[:22]
        out.append(f"{label:>5}  {side}  {chk_am(h.get('entry'))}")
        shown.append(h)
    if not shown:
        return None
    extra = len(rows) - len(shown)
    out.append("")
    out.append(f"+{extra} more" if extra > 0 else f"{len(shown)} plays")
    text = "\n".join(out)
    while len(text) > 275 and len(shown) > 1:      # trim until it fits
        shown.pop()
        return board_post(rows, label, max_rows=len(shown))
    return text


def chk_am(entry):
    import check as chk
    return chk.am(entry or 0)


def pick_post(h):
    """Pre-game post for one play. Alias + blurred figures only."""
    u, _, _ = U.recommend(h)
    import postplays as P, check as chk
    side = P.play_text(h)
    lines = [f"{side}  {chk.am(h.get('entry') or 0)}",
             f"{U.fmt(u)} · {anon.alias(h.get('user'))} · {anon.pct(h.get('hold'))} hold",
             f"{anon.money(h.get('stake'))} on it"]
    vt = h.get("vs_typical")
    if vt and (h.get("n_open") or 0) >= U.MIN_POSITIONS and (h.get("typical") or 0) >= U.MIN_TYPICAL:
        lines.append(f"{vt:.0f}x their normal size")
    lines.append("")
    lines.append(h.get("game", ""))
    return "\n".join(x for x in lines if x is not None)


def main():
    a = sys.argv[1:]
    send = "--send" in a
    text = image = None

    if "--check-media" in a:
        # uploads an image and posts NOTHING — proves the media endpoint and
        # the app's write permission without publishing anything
        mid, detail = upload_media(a[a.index("--check-media") + 1])
        print(f"  media upload  {'OK  id ' + mid if mid else 'FAILED  ' + detail}")
        return 0 if mid else 1

    if "--check" in a:
        missing = [c for c in CREDS if not _quiet(c)]
        if missing:
            print("  NOT configured: " + ", ".join(missing))
            print("\n  store each with:")
            for c in missing:
                print(f"    security add-generic-password -U -s {SERVICE} -a {c} -w")
            return 1
        ok, detail = verify()
        print(f"  credentials  {'VALID' if ok else 'REJECTED'}  {detail}")
        if ok:
            print("  NOTE: this proves READ access only. If the app permission was")
            print("  left on Read-only, posting still fails 403 — see the header.")
        return 0 if ok else 1

    if "--board" in a:
        src = a[a.index("--board") + 1]
        label = a[a.index("--label") + 1] if "--label" in a else "BOARD"
        rows = [r for r in json.load(open(src)) if (r.get("conv") or 0) >= 65]
        # same trust filter the published picks use — a board that headlines a
        # $8-median wallet at 5u is the most embarrassing thing on the feed
        rows = [r for r in rows
                if (r.get("n_open") or 0) >= U.MIN_POSITIONS
                and (r.get("typical") or 0) >= U.MIN_TYPICAL]
        text = board_post(rows, label)
        if not text:
            print("nothing qualifies — not posting")
            return 0
        if "--image" in a:
            # The card is rendered in the dry run too, so what would be posted
            # can be LOOKED at first. A failed render never blocks the post:
            # the text headline still goes out on its own.
            import xcard
            card = os.path.join("/tmp", f".xcard-{os.getpid()}.png")
            ok, detail = xcard.render(rows, label, card)
            print(f"card: {card + ' — ' + detail if ok else 'FAILED — ' + detail}")
            image = card if ok else None
    elif "--pick" in a:
        src = a[a.index("--pick") + 1]
        rows = json.load(open(src))
        rows = [r for r in rows if (r.get("conv") or 0) >= 70]
        if not rows:
            print("no qualifying play")
            return 0
        text = pick_post(max(rows, key=lambda r: r.get("conv") or 0))
    elif "--daily" in a:
        day = (a[a.index("--day") + 1] if "--day" in a
               else (datetime.now().date()).isoformat())
        text = scoreboard(load(day=day), f"TODAY · {day}")
    elif "--monthly" in a:
        mo = (a[a.index("--month") + 1] if "--month" in a
              else datetime.now().strftime("%Y-%m"))
        text = scoreboard(load(month=mo), f"{datetime.strptime(mo,'%Y-%m'):%B} so far")
    else:
        print(__doc__.strip().split("\n\n")[1])
        return 1

    if not text:
        print("nothing settled for that period — nothing to post")
        return 0

    print("-" * 46)
    print(text)
    print("-" * 46)
    print(f"{len(text)} chars")
    if not send:
        print("\nDRY RUN. Add --send to post. Nothing has been published.")
        return 0
    if not have_creds():
        print("\nX credentials not configured — see the header for the four "
              "keychain commands.", file=sys.stderr)
        return 2
    ok, why, had = post_with_image(text, image)
    print(("posted" + (" with image" if had else "")) if ok else f"FAILED — {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
