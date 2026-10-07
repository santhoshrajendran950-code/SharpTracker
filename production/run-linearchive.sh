#!/bin/bash
# Price archive poll. Hourly 9am-11pm local.
#
# Cost is self-limiting: each sport is probed with one h2h call (2 credits) and
# the other markets are only fetched when a game starts within LOOKAHEAD_H, so
# off-season and overnight polls cost 2 credits a sport instead of 6.
#
# Budget: 3 sports x 3 markets = 18 credits a poll when everything is active.
# 15 polls a day = 270/day = ~8,100/month against a 20,000 plan.
cd "$(dirname "$0")" || exit 1
LOG=linearchive.log
/usr/bin/python3 linearchive.py >> "$LOG" 2>&1 || \
  echo "[$(date '+%F %T %Z')] archive poll FAILED" >> "$LOG"
exit 0
