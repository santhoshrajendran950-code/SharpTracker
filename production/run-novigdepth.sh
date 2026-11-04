#!/bin/bash
# Novig resting-order archive, every 15 min. The dataset for the Unjuiced
# "Sharp Money" lean test (novigdepth.py), which cannot be collected
# retroactively. Read-only: catalog + order books on the read key, nothing
# traded, nothing posted.
#
# 12h horizon, not 24: resting size builds toward game time, and a book 24h out
# is mostly empty rows that triple the archive for no signal.
cd "$(dirname "$0")" || exit 1
LOG=novigdepth.log
/usr/bin/python3 novigdepth.py --horizon 12 >> "$LOG" 2>&1
exit 0
