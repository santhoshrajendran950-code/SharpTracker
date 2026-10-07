#!/usr/bin/env python3
"""
Email sender for the server deployment.

Replaces the macOS path (`claude -p` + the Gmail MCP connector), which needs an
authenticated CLI session and is too fragile for unattended use on a headless
box. Two backends, chosen by whichever env vars are set:

  MAIL_BACKEND=resend   RESEND_API_KEY, MAIL_FROM
  MAIL_BACKEND=smtp     SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM

Recipients:
  MAIL_TO    comma-separated, visible
  MAIL_BCC   comma-separated, blind

    python3 mailer.py "subject" body.txt
    echo "body" | python3 mailer.py "subject"

Exits non-zero on failure so the caller can log it. Never prints credentials.
"""
import json
import os
import smtplib
import ssl
import subprocess
import sys
from email.message import EmailMessage


def _split(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def send_resend(subject, body, to, bcc):
    key = os.environ["RESEND_API_KEY"]
    payload = {
        "from": os.environ.get("MAIL_FROM", "Nightly Line <onboarding@resend.dev>"),
        "to": to,
        "subject": subject,
        "text": body,
    }
    if bcc:
        payload["bcc"] = bcc
    p = subprocess.run(
        ["curl", "-s", "-o", "/tmp/.mailer.out", "-w", "%{http_code}",
         "--max-time", "30", "-X", "POST", "https://api.resend.com/emails",
         "-H", f"Authorization: Bearer {key}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(payload)],
        capture_output=True, text=True)
    code = p.stdout.strip()
    if code.startswith("2"):
        return True, f"resend {code}"
    try:
        detail = open("/tmp/.mailer.out").read()[:200]
    except OSError:
        detail = ""
    return False, f"resend HTTP {code} {detail}"


def send_smtp(subject, body, to, bcc):
    msg = EmailMessage()
    msg["From"] = os.environ["MAIL_FROM"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        # bcc recipients go in the envelope only, never in a header
        s.send_message(msg, to_addrs=to + bcc)
    return True, "smtp ok"


def main():
    if len(sys.argv) < 2:
        print("usage: mailer.py <subject> [body-file]", file=sys.stderr)
        return 2
    subject = sys.argv[1]
    body = (open(sys.argv[2]).read() if len(sys.argv) > 2 else sys.stdin.read())

    to = _split(os.environ.get("MAIL_TO"))
    bcc = _split(os.environ.get("MAIL_BCC"))
    if not to:
        print("MAIL_TO is not set", file=sys.stderr)
        return 2

    backend = os.environ.get("MAIL_BACKEND", "resend").lower()
    try:
        ok, detail = (send_resend if backend == "resend" else send_smtp)(
            subject, body, to, bcc)
    except KeyError as e:
        print(f"missing env var for {backend} backend: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"{backend} send failed: {e}", file=sys.stderr)
        return 1
    print(detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
