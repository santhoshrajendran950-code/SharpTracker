# Direct exchange prices: ProphetX and Novig

Added 2026-10-12. Until now every Novig and ProphetX price on the board came
through The Odds API (`region=us_ex`), which is a polled snapshot that can be a
minute or more stale on a thin exchange. `production/prophetx.py` and
`production/novig.py` quote the two exchanges directly; `books.py` overlays
those quotes on the aggregator's table whenever credentials are present and
falls back silently when they are not. Nothing changes for the Odds API path.

```
books.prices_for(row)
  └─ _odds_api_prices(row)         every book, one call per sport+market
  └─ _overlay_direct(row, rows)    prophetx.prices_for / novig.prices_for
        replaces the PX / NOVIG entry with the live quote, re-sorts best-first
```

`DIRECT_EXCHANGES=0` disables the overlay. `python3 books.py` prints where each
credential was found (never the value).

---

## ProphetX

Developer hub: <https://docs.prophetx.co>. Two routes in; the first is the one
to ask for.

### Option A: Market Data API (read-only, one key)

1. Email the ProphetX Market Data team and ask for an **affiliate API key** for
   a read-only price-display integration:
   `jordan.grzeczka@prophetexchange.com` (the address printed on the
   [Market Data Integration](https://docs.prophetx.co/docs/market-data-integration)
   page). Mention that you are an account holder and want production access;
   ask for sandbox too if you want to test first.
2. When the key arrives, store it in the login Keychain without pasting it
   into a transcript:
   ```
   read -s -p "key: " K && security add-generic-password -U -a affiliate -s prophetx -w "$K" && unset K
   ```
3. Smoke test:
   ```
   python3 ~/.nightly-line/prophetx.py        # tournaments + one NFL market
   python3 ~/.nightly-line/prophetx.py NFL    # events ProphetX lists for a league
   ```
   `PROPHETX_ENV=sandbox python3 prophetx.py` points at the sandbox instead.

The key goes in the `Authorization` header raw, with no `Bearer` prefix. Base
URLs: production `https://cash.api.prophetx.co/partner`, sandbox
`https://api.sandbox.prophetx.dev/partner`. Endpoints used:
`/affiliate/get_tournaments?has_active_events=true`,
`/affiliate/get_sport_events?tournament_id=`,
`/v4/affiliate/get_markets?event_id=&market_types=moneyline|spread|total`.
Prices are American odds; `quantity` is what is available at that price.
A `429` is reported on stderr and the Odds API quote is kept for that row.

### Option B: Trading API (access key + secret)

Only needed if ProphetX will not issue an affiliate key, or later for order
placement.

1. Sandbox first: register at <https://sandbox.prophetx.dev/signup> (2FA, so
   use a real phone number).
2. Email `marketmaking@prophetexchange.com` (or `hello@prophetexchange.com`)
   and ask them to flag the account as an **API account** and add test funds.
   For production, make the same request against your real ProphetX account.
3. Log in, click the top-right toggle, open **Menu → API integration**, then
   **create a new token**. You get an **Access Key** and a **Secret Key**.
   Create one pair per process: ProphetX counts live sessions per key, and a
   revoked token still occupies its slot until it expires.
4. Store both:
   ```
   read -s -p "access: " K && security add-generic-password -U -a access -s prophetx -w "$K" && unset K
   read -s -p "secret: " K && security add-generic-password -U -a secret -s prophetx -w "$K" && unset K
   ```
5. `python3 prophetx.py` logs in (`POST /auth/login`), caches the 20-minute
   bearer token in `~/.nightly-line/.prophetx-session` so the 15-minute jobs
   reuse it, and reads the `/mm/...` endpoints.

---

## Novig

Developer docs: <https://docs.novig.com>. Novig has no API-key form to fill in.
Any verified account creates its own keys from the web profile; every request
is signed with an Ed25519 keypair (scheme `NOVIG-V3`). The signer in
`novig.py` uses OpenSSL 3 only and is checked against all 30 of Novig's
published test vectors (`python3 novig.py` runs the `/v3/echo` check).

Reading the catalog and order books requires a `trading::read` key, which can
read prices but never place an order or move money. That is the only key this
Mac will hold.

1. **Account.** Log in at <https://novig.com>. For a dry run first, sign up at
   <https://paper.novig.com> (play money, no identity check) and do the same
   steps there with `NOVIG_HOST=https://api.paper.novig.com`.
2. **Management key.** Open **Profile → Settings → Novig API**, press
   **Create Key**, give it a nickname, **Confirm Key Creation**. The browser
   downloads `novig-api-key-<nickname>.pem` and shows the private key once.
   Save that file in your password manager, note the **key ID** shown beside
   it, then press **I've saved my key**. The screen also shows the result of
   Novig's location check. Only one management key can be live per account.
3. **Read-only key for this Mac.** From the folder holding the management PEM:
   ```
   python3 ~/.nightly-line/novig.py setup <MANAGEMENT_KEY_ID> novig-api-key-<nickname>.pem
   ```
   This signs an echo with the management key, opens a subaccount named
   `sharptracker` (destroying the trading key it is born with), generates
   `~/.nightly-line/.novig-read.pem` (chmod 600), registers its public half as
   a `trading::read` key, and stores the resulting key ID in the Keychain
   under `novig/key-id`. The management PEM is used only during this step;
   delete it from the Mac afterwards.
4. **Smoke test:**
   ```
   python3 ~/.nightly-line/novig.py          # echo, leagues, market types, 3 NFL books
   python3 ~/.nightly-line/novig.py MLB      # open markets for a league
   ```
5. Nothing else to configure. `books.py` picks the key up on the next board.

**Status 2026-10-12: live.** Management key `sharptracker-mgmt` created on
novig.com, subaccount `sharptracker` opened, `trading::read` key stored in the
Keychain. Verified against the real API on NFL, MLB and NHL rows; every direct
quote matched the Odds API's Novig price to the cent, with a dollars-available
figure the aggregator cannot give.

What the real API looks like (differs from the docs' examples):

- `GET /v3/types/leagues` returns a bare list: `NFL, NBA, MLB, NHL, NCAAF,
  NCAAB, WNBA, ATP, WTA, EPL, …`. Market types `MONEY, SPREAD, TOTAL` plus
  `MONEY_1H`, `SPREAD_1H`, `TOTAL_1H`, `TEAM_TOTAL` and hundreds of props.
- Markets carry only abbreviations: moneyline description `LAR`, outcomes
  `LAR` / `BUF`; spread `LAR -3.5` with outcomes `LAR -3.5` / `BUF +3.5`
  (one market per line, `strike` is the home handicap); total `BUF @ LAR t54.5`
  with outcomes `Over 54.5` / `Under 54.5`. Full names are on the event:
  `GET /v3/catalog/events?league=&startsAfter=&startsBefore=` gives
  `description: "Buffalo Bills @ Los Angeles Rams"` (away @ home).
  `novig.py` therefore matches the event first, then
  `GET /v3/catalog/markets?event=<id>&marketType=`, then resolves the outcome
  by abbreviation (initials / prefix heuristic, falling back to eliminating
  the opponent, which covers `JAX`, `WSH`).
- `GET /v3/catalog/markets/{id}/book` returns `{marketId, seq, orders: {outcomeId: [{orderId, price, qty}]}}`,
  best first. `qty` is in hundredths of a $1 contract. The taker price for a
  side is `1 - best resting bid on the other outcome`; the dollars available
  at that price are `sum(qty at best) / 100 × price`.
- Read throttle: 64-request burst refilling at 16 a second; `429` carries
  `Retry-After`.

Location checks (production only): every signed call refuses VPN/proxy IPs
and Arizona, Michigan and Nevada with a `451`. Reads are admitted even when
the phone's companion geolocation is stale; placements are not.

### Placing orders: `place.py` (paper exchange by default)

Added 2026-10-12 evening. `production/place.py` turns a graded pick into a
Novig limit order. It is pointed at **paper.novig.com** unless both `--real`
is on the command line and `NOVIG_REAL=1` is in the environment; otherwise it
exits before signing anything.

```
place.py quote  NFL "Buffalo Bills vs Los Angeles Rams" Bills "Spread: Bills +3.5" 2026-10-13T00:15Z
place.py order  NFL "Buffalo Bills vs Los Angeles Rams" Bills "Spread: Bills +3.5" 2026-10-13T00:15Z --units 1 --odds -113 [--dry-run]
place.py from-picks [--since-min 40] [--dry-run]      new picks.jsonl rows -> orders
place.py status | cancel <orderId> | cancel --all
```

How it prices: the pick's `odds` is the worst price accepted (`prob_max` =
implied probability + `slack`, default 0.005). If the best resting offer on
the other outcome gives a price at or under that, the order **takes** it
(pregame takers pay no fee on game markets, `fee.charged == WHEN_LIVE`).
Otherwise it **rests** at `prob_max` as a maker. The exchange cancels every
resting order at go-live, so a GTC order can never fill in-game. Prices are
snapped down to Novig's grid (0.005 steps mid-range); `qty` is in 1-cent
contracts, so $10 at 0.530 is 1,886 contracts.

Hard limits live in `~/.nightly-line/place.json` (defaults: `unit_dollars`
10, `max_order_dollars` 50, `max_day_dollars` 200, `slack` 0.005, US leagues
only). Every attempt is appended to `orders.jsonl` with the plan, the book
seen, the request and the answer; a pick (`slug|pick`) is never sent twice,
because Novig's `clientId` is a label, not an idempotency key.

Dry-run planner output against live production books, 2026-10-12 evening:

| Row | Pick odds | Book | Decision |
|---|---|---|---|
| Bills +3.5 | -113 | 0.530 (-113), $109K | TAKE 1,886 @ 0.530 = $10.00 |
| Bills ML | +170 | 0.390 (+156) | REST 5,333 @ 0.375 (+167) = $20.00 |
| Guardians +1.5 | -150 | 0.590 (-144), $14K | TAKE 1,694 @ 0.590 = $9.99 |
| Knights ML, 6u | -110 | — | refused: $60 > max_order_dollars |

**Paper setup (one time).** Keys are per environment, so Paper needs its own:

1. Sign up at <https://paper.novig.com> (no identity check; press *Fill in
   KYC Data, Accept all Terms, and Start with $10,000*).
2. Profile → Settings → Novig API → Create Key, same as production. Keep the
   downloaded PEM and the key ID.
3. ```
   NOVIG_HOST=https://api.paper.novig.com python3 ~/.nightly-line/novig.py setup <KEY_ID> <pem> sharptracker --trading --fund 500
   ```
   `--trading` keeps the subaccount's trading key on the Mac (it is play
   money); `--fund 500` moves $500 of it into the subaccount. Keys land in
   the Keychain as `novig/*-paper` and PEMs as `.novig-*-paper.pem`.
4. `NOVIG_HOST=https://api.paper.novig.com python3 ~/.nightly-line/place.py status`

To go live later you would repeat step 3 against production **without**
`--fund` first, fund by hand, and run `place.py` with `--real` and
`NOVIG_REAL=1`. Production placements also need the Novig phone app to have
geolocated within 3 days.

**Also found:** `/v3/public/catalog/*` and `/v3/public/types/*` answer
without any signature on both hosts, so quotes do not strictly need a key.
`novig.py` keeps using the signed routes (own throttle bucket).

### Public data (no key)

Novig publishes anonymised daily CSVs at `https://data.novig.com/reporting/trade-data/`
(`index.json`, then `<date>/trades.csv` and `<date>/markets.csv`, published
about 5am Eastern for the previous day). Every trade has `cost`, `qty`,
`league`, `marketType`, `TAKER`/`MAKER` side. No trader identity, so it cannot
feed the sharp-money signal, but it is a free volume and closing-price
history for CLV research. `python3 novig.py public 2026-10-11` downloads a day
into `~/.nightly-line/novig-public/`. As of 2026-10-12 there are 70 trade days
available.

---

## Files and secrets

| Path | What | Committed? |
|---|---|---|
| `production/exkeys.py` | env → Keychain → file credential lookup | yes |
| `production/prophetx.py`, `production/novig.py` | the clients | yes |
| `~/.nightly-line/.novig-read.pem`, `.novig-read.pub.pem` | Novig read-only keypair | never (`*.pem` ignored) |
| `~/.nightly-line/.novig-mgmt.pem` | Novig management key (can move funds; used only by `setup`) | never; belongs in a password manager, not on the Mac |
| `~/.nightly-line/.prophetx-session` | cached ProphetX bearer token | never |
| Keychain `prophetx/{affiliate,access,secret}`, `novig/key-id` | the keys | n/a |

The two exchanges stay in `MY_BOOKS` in `books.py`: Santhosh can bet both, so
their quotes compete for the BEST price the same way they did through the
aggregator.
