#!/usr/bin/env python3
"""
The one email sender. Every job that mails goes through this (via sendmail.sh).

Replaces `claude -p` + the Gmail MCP connector, which needs an authenticated CLI
session and a model call per email: not deterministic, and too fragile for
unattended use or for anything a subscriber pays for. Two backends:

  MAIL_BACKEND=resend   RESEND_API_KEY, MAIL_FROM          (default)
  MAIL_BACKEND=smtp     SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM

Each setting resolves from the environment first, then the macOS Keychain
(service "nightly-line-mail", account = the variable name, e.g.
`security add-generic-password -s nightly-line-mail -a RESEND_API_KEY -w`),
so the Mac and the server run the same code and nothing sits in a file in the tree.

Recipients: --to / --bcc (comma-separated) from the calling script, else
MAIL_TO / MAIL_BCC. BCC addresses go in the envelope only, never in a header.

    python3 mailer.py "subject" body.txt --to a@x.com --bcc b@y.com
    echo "body" | python3 mailer.py "subject"

Exit codes: 0 sent · 1 send failed after retries · 2 not configured (the
caller may fall back). Every failure is appended to mail-failed.jsonl beside
the caller's working directory so the watchdog can see it. Never prints
credentials.
"""
import json
import os
import smtplib
import ssl
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

KEYCHAIN_SERVICE = "nightly-line-mail"
TRIES = 3
BACKOFF = 5            # seconds, doubled per retry
FAILED_LOG = "mail-failed.jsonl"


class NotConfigured(Exception):
    pass


class Retryable(Exception):
    pass


def setting(name, default=None):
    v = os.environ.get(name)
    if v:
        return v
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                            "-a", name, "-w"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass                      # not a Mac, or the keychain is locked
    return default


def need(name):
    v = setting(name)
    if not v:
        raise NotConfigured(name)
    return v


def _split(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def send_resend(subject, body, to, bcc, idem):
    payload = {"from": need("MAIL_FROM"), "to": to, "subject": subject, "text": body}
    if bcc:
        payload["bcc"] = bcc
    # The key goes to curl on stdin as a header file, never on the command line
    # where `ps` would show it. The idempotency key makes a retry after a timeout
    # safe: Resend sends at most once per key.
    hdr = (f"Authorization: Bearer {need('RESEND_API_KEY')}\n"
           f"Content-Type: application/json\nIdempotency-Key: {idem}\n")
    p = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}", "-X", "POST",
         "https://api.resend.com/emails", "-H", "@-", "--data-binary", json.dumps(payload)],
        input=hdr, capture_output=True, text=True)
    out, _, code = p.stdout.rpartition("\n")
    if code.startswith("2"):
        return f"resend {code}"
    if code in ("000", "429") or code.startswith("5"):
        raise Retryable(f"resend HTTP {code} {out[:200]}")
    raise RuntimeError(f"resend HTTP {code} {out[:200]}")


def send_smtp(subject, body, to, bcc, idem):
    msg = EmailMessage()
    msg["From"] = need("MAIL_FROM")
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{idem}@nightly-line>"
    msg.set_content(body)
    host, port = need("SMTP_HOST"), int(setting("SMTP_PORT", "587"))
    user, pw = need("SMTP_USER"), need("SMTP_PASS")
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, pw)
            s.send_message(msg, to_addrs=to + bcc)
    except (OSError, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError) as e:
        raise Retryable(f"smtp {e}")
    return "smtp ok"


def record_failure(subject, to, why):
    try:
        with open(FAILED_LOG, "a") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "subject": subject, "to": to, "why": why[:300]}) + "\n")
    except OSError:
        pass


def main():
    a = sys.argv[1:]
    flags = {}
    for name in ("--to", "--bcc"):
        if name in a:
            i = a.index(name)
            flags[name] = a[i + 1] if i + 1 < len(a) else ""
            del a[i:i + 2]
    if not a:
        print("usage: mailer.py <subject> [body-file] [--to a,b] [--bcc c,d]", file=sys.stderr)
        return 2
    subject = a[0]
    body = open(a[1]).read() if len(a) > 1 else sys.stdin.read()
    to = _split(flags.get("--to") or setting("MAIL_TO"))
    bcc = _split(flags["--bcc"]) if "--bcc" in flags else _split(setting("MAIL_BCC"))
    if not to:
        print("no recipient: pass --to or set MAIL_TO", file=sys.stderr)
        return 2

    backend = (setting("MAIL_BACKEND", "resend") or "resend").lower()
    send = send_resend if backend == "resend" else send_smtp
    idem = str(uuid.uuid4())
    why = ""
    for attempt in range(TRIES):
        try:
            print(send(subject, body, to, bcc, idem))
            return 0
        except NotConfigured as e:
            print(f"mail not configured: {backend} backend needs {e}", file=sys.stderr)
            return 2
        except Retryable as e:
            why = str(e)
            print(f"{backend} attempt {attempt + 1}/{TRIES} failed: {why}", file=sys.stderr)
            if attempt + 1 < TRIES:
                time.sleep(BACKOFF * 2 ** attempt)
        except Exception as e:
            why = f"{type(e).__name__}: {e}"
            print(f"{backend} send failed: {why}", file=sys.stderr)
            break
    record_failure(subject, to, why)
    return 1


if __name__ == "__main__":
    sys.exit(main())
