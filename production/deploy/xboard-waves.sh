#!/bin/bash
# Server replacement for the 18 per-wave launchd jobs (xboard00-17): one timer
# fires at every wave time, this picks the league + label from the clock.
cd "$(dirname "$0")/.." || exit 1
DOW=$(date +%u); HM=$(date +%H:%M)
case "$DOW:$HM" in
  4:20:0*) ARGS=(all "BIG BOARD · Thursday night") ;;
  4:20:1*) ARGS=(nfl "NFL BOARD · TNF kickoff") ;;
  6:11:4*) ARGS=(all "BIG BOARD · T-15 to the noon wave") ;;
  6:12:0*) ARGS=(all "BIG BOARD · noon wave kickoff") ;;
  6:15:1*) ARGS=(all "BIG BOARD · T-15 to the 3:30 wave") ;;
  6:15:3*) ARGS=(all "BIG BOARD · 3:30 wave kickoff") ;;
  6:18:4*) ARGS=(all "BIG BOARD · T-15 to the 7:00 wave") ;;
  6:19:0*) ARGS=(all "BIG BOARD · 7:00 wave kickoff") ;;
  6:21:4*) ARGS=(all "BIG BOARD · T-15 to the late wave") ;;
  6:22:0*) ARGS=(all "BIG BOARD · late wave kickoff") ;;
  7:12:4*) ARGS=(all "BIG BOARD · T-15 to the 1:00 wave") ;;
  7:13:0*) ARGS=(all "BIG BOARD · 1:00 wave kickoff") ;;
  7:15:5*) ARGS=(all "BIG BOARD · T-15 to the 4:05 wave") ;;
  7:16:0*) ARGS=(all "BIG BOARD · 4:05 wave kickoff") ;;
  7:20:0*) ARGS=(all "BIG BOARD · T-15 to SNF") ;;
  7:20:2*) ARGS=(all "BIG BOARD · SNF kickoff") ;;
  1:20:0*) ARGS=(all "BIG BOARD · Monday night") ;;
  1:20:1*) ARGS=(nfl "NFL BOARD · MNF kickoff") ;;
  *) echo "[$(date '+%F %T %Z')] xboard-waves: no wave at $DOW $HM" >> xboard.log; exit 0 ;;
esac
exec ./run-xboard.sh "${ARGS[@]}"
