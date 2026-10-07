# EXP-NNN — <one-line hypothesis>

- **Status:** proposed | running | adopted | rejected | parked
- **Proposed:** YYYY-MM-DD by <Santhosh | weekly review>
- **Rule it would change:** <docs/alert-rules.md section, file:constant>
- **Prior evidence:** <which cut/window suggested it, with n and CI>

## Hypothesis
One sentence. State the direction ("totals should NOT be sized down") and the
number that would confirm it.

## Test
- Data: <live ledger window | research windows (which sports/months)>
- Method: <script + flags>. Out of sample means the rows played no part in
  choosing the rule; say which rows those are.
- Pass: <metric, threshold, minimum n/games>. CLV first, ROI second.
- Fail: <what result retires it>

## Result
Numbers, with game-clustered CIs. Paste the table. "Nothing survives" is a
legitimate result; write it.

## Decision
Adopted (what changed, where, date) | Rejected (why) | Parked (what it needs).
