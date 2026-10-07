#!/bin/bash
# Weekly bet ledger -> one Google Sheet per week, in the "Nightly Line Ledgers"
# folder in YOUR_EMAIL@example.com. Monday 08:00 local.
#
# Each sheet is TITLED with that week's Monday date (e.g. 2026-09-14) and covers
# the Mon-Sun week that has just closed, so the report never describes a week
# still in progress.
#
# Delegates to settle.refresh_sheet() so a re-run REPLACES that week's sheet
# instead of creating a second file with the same title.
#
# WHY A FOLDER OF SHEETS, not one workbook with a tab per week: the Drive MCP
# exposes Drive, not Sheets. A tab cannot be appended to a live spreadsheet, and
# uploading a multi-sheet .xlsx was rejected by the connector (base64Content ->
# "Request contains an invalid argument"). CSV converts to a Sheet cleanly, so
# one CSV per week is the route that actually works.
#
# PRIVATE. This is Santhosh's own money. It goes to his own Drive and NOWHERE
# else: no email (those have BCC'd recipients), no Discord (discordsink.py
# refuses bet-log content by design). Do not add a recipient to this script.
cd "$(dirname "$0")" || exit 1

FOLDER_ID="YOUR_DRIVE_FOLDER_ID"     # "Nightly Line Ledgers"
CLAUDE="/Users/santhosh/.local/bin/claude"
LOG=weeksheet.log
CSV=/tmp/.weeksheet.csv

# The Monday of the week that just ended = 7 days before this Monday.
WEEK=$(/usr/bin/python3 -c "
from datetime import date, timedelta
t = date.today(); m = t - timedelta(days=t.weekday())
print((m - timedelta(days=7)).isoformat())")

/usr/bin/python3 -c "import settle; settle.refresh_sheet('$WEEK')" >> "$LOG" 2>&1
echo "[$(date '+%F %T %Z')] weekly sheet $WEEK refreshed" >> "$LOG"
exit 0
