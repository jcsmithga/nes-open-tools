# Competitive play: data model

> **Note**: This document was written by Claude, from a design session with jdharms on
> 2026-11-16, updated for the ratings decisions of 2026-11-17. It is the storage for [design.md](design.md): what a daily, its ratings and
> its results keep, and when each is fixed. Milestone A's tables are built (migration 9:
> `dailies`, `daily_seeds` and `daily_cards` without handicaps, in `server/dailies.py`),
> and so are the ratings tables (migration 10, in `server/ratings.py`); the rest are
> proposed, their column lists the shape and not the final migration.

## The rule

Every result is reproducible from rows that were written once and never changed. What a
daily depends on keeps changing: hole ratings are refitted daily, player indexes move as
rounds are scanned, the rules' numbers are tuned after the backtest, and the weekday
requests and rollover hour may change. A daily therefore copies what it uses at the
moments it is fixed:

| Moment | What is fixed | Tables |
|--------|---------------|--------|
| A seed's generation | Its course rating, slope and hole ratings, never redone | `seed_ratings`, `seed_hole_ratings` |
| Open | The day's window, request, seed, the scoring and index rules | `dailies`, `daily_seeds` |
| Scan | The index the round is scored with, the handicap, the net card, and the player's index after it | `daily_cards`, `daily_card_holes` |
| Close, and any later void or restore | The standing, matches and skins | `daily_results`, `daily_matches` |

This follows ADR 0024, where a seed is what the site stored rather than a recipe to
rebuild: a daily is what was stored when it opened, when each round arrived and when it
was scored, not something the current model and rules recompute.

Not fixed: Discord names and avatars. Records follow a player under their current name,
as every other page does.

## Ratings runs

The daily refit (task 11), at the rollover in the app's loop, writes a new run and never
edits an earlier one. It fits every recorded slot-0 round on any seed, flagged rounds
and withdrawn seeds' included, and writes no run below 20 rounds.

| Table | Holds |
|-------|-------|
| `ratings_runs` | id, the rollover day it was fitted for (unique, so a restart does not fit again), created_at, the model version, the parameters file's hash and a JSON copy of its values, the fitted model's own numbers as JSON (the fixed coefficients for intercept, par, distance, wind and mirrored, the distance centers, the reference holes' mean par, the field's skill, the luck, the iterations and the hole scores used), the latest `received_at` the fit saw, the number of rounds it used |
| `ratings_effects` | run, group (layout, terrain or pin), level, value: every random effect the run fitted. Only levels the run saw rounds for |
| `seed_ratings` | seed (primary key), the run that rated it, rated_at, par, course rating, slope. Written once and never rewritten (implementation D47) |
| `seed_hole_ratings` | seed, position: rating, spread factor, stroke index |

A run keeps the model's parts rather than each hole's finished rating, because a hole's
rating depends on its slot: the wind is drawn per slot, and a level the run has not seen
falls back on its parent, an unseen terrain on its layout and an unseen pin on its
terrain, as in the fit (implementation D68). A run is rebuilt into the fitted model and
rates a seed as `golf-ratings` and the backtest do.

A seed is rated when it is generated, under the latest run, in the transaction that
stores it. That is lookups and arithmetic, not a fit. A seed made while no run exists,
before launch or on a fresh database, is stored unrated, and every refit rates the
seeds that are, so the first refit after the launch's deploy rates every seed on the
site (task 12). Its stored ratings are what every round on it is scored against, daily
or not: a round's differential uses them, so it never changes, and a player's index
moves only when they play. They also serve a player's handicap for any seed (task 24).

The hole keys are the spike model's (`tools/spikes/player_skill.md`): `hole_id`,
`data_hash` and the pin index, all on `seed_holes`. A hole mirrored and otherwise
unchanged is keyed by its unmirrored twin's data hash (design.md, Rating holes and
courses).

Every run is kept, effects included: about two thousand rows a night at the 2026-11
catalog size, a few megabytes a year. A seed keeps its own ratings, so nothing depends on
an old run's effects, but they show how a hole's rating moved and let a backtest or a
question about an old day be answered from the database.

## Dailies

| Table | Holds |
|-------|-------|
| `dailies` | `day` (primary key: the daily's own date, `YYYY-MM-DD`), the theme of the schedule entry it came from (a weekday or a dated override) as it was then: `theme_name` and the optional `theme_description`, the request as it was read (ADR 0016), `opens_at` and `closes_at` in UTC, created_at, withdrawn_at. Milestone D adds `rules` and closed_at |
| `daily_seeds` | day, attempt (1, or 2 for a rerun), seed (unique), replaced_at. The day's seed is the one not replaced. A seed's pages know it is a daily, and hide its scores, from this table |

- **The request is copied** because the checked-in weekday requests change, and a past
  day keeps the request it was drawn from. The concrete settings are already in the
  seed's manifest.
- **The window is stored per day**, so a later change to the rollover hour or the
  window's length leaves past days as they ran. Windows overlap by an hour (design.md,
  The window); the Daily Seed is the open one, not withdrawn, with the latest date.
- **Open and closed are read from the window**, not stored: a day is open from
  `opens_at` until `closes_at`. Milestone D's close (task 22) adds closed_at, when the
  day's results are first fixed.
- **`rules`** is a JSON copy of the numbers in force at open: the allowance, the
  Stableford table, the formats scored, and the index rules a scan on the day applies
  (the window, the starting index and its weight, the hole cap, exceptional score
  reduction, the low-mark caps, and the low mark's window and minimum rounds).
- **The PRNG seed** is a keyed hash of the date under the server secret, cut to the
  manifest's 16 hex characters. The manifest publishes it, so it must not reveal the
  secret or any other day's.
- **Magic words** are the date (design.md, Magic words), set on the manifest by the
  daily job.

### Reruns and withdrawals

A rerun is for a seed nobody can complete. Its scorecard QR comes at the end of a round,
so a round recorded on the day's seed shows it can be completed, and then the day is
withdrawn instead: replacing it would be unfair to the players who already submitted.

- Replacing is refused when attempt 1 has a card. A day is replaced at most once.
- Rounds that reach attempt 1 after it is replaced are ordinary rounds on that seed.
- A withdrawn day counts toward nothing: no results, no records, and its rounds do not
  count toward any player's index (jdharms, 2026-11-16). Its cards are kept as history.

Both actions go through the audit log.

## Cards

A card is written when the server accepts a player's own round (slot 0) on the day's
current seed while the day is open and not withdrawn. Its existence is what makes a round
a daily round. A teammate's round on someone's ROM (slot 1) gets none: the site does not
know who played it.

| Table | Holds |
|-------|-------|
| `daily_cards` | day, user (unique together), the round's `public_id`, created_at. Milestone D adds the index the round was scored with, the daily handicap, and the player's index and low mark after it |
| `daily_card_holes` | day, user, position: handicap strokes received, net score, Stableford points. Milestone D |

- **The round is referenced by `public_id`**, which a void and restore keep, not by row
  id, which they change.
- **A card is never deleted.** It counts while its round is in `rounds`; a voided round's
  card is ignored until the round is restored. A new round in the slot the void freed,
  received while the day is open, takes the card over.
- **A round after close** is an ordinary round on the seed and gets no card, as does a
  round on a withdrawn day.
- **A player's counted rounds** are their rounds with a card on a day not withdrawn,
  Milestone A's dailies included, and no others: every player's index starts from the
  starting index. A change to a day's window never changes which rounds counted.
- **An index is a pure function** of a player's counted rounds, in the order received,
  their seeds' stored ratings and the index rules. A scan recomputes it from them, which
  is cheap, rather than updating a stored value, so a void, a restore, a withdrawn day
  and the launch need nothing special. The index on the card before the round is the
  one it was scored with: the player's index after their previous counted round, so
  with one daily a day, the index they held when the daily opened. A player who scans
  the older daily in the hour two overlap carries its update into the newer one.
- **A player's cards are their index history**, one row per counted round: the low mark
  is read from it.

The values on a card could be computed again from the rounds, the seeds' ratings and the
rules. They are stored because they are what the player was shown that day, and so
records are plain SQL over rows.

## Results

| Table | Holds |
|-------|-------|
| `daily_results` | date, user: Stableford points, position (ties shared), skins won, whether they won the day |
| `daily_matches` | date, the two users: holes won by each, the result (win, half, loss) and its margin. Every pair of cards |

- **Scoring is a pure function** of the day's cards whose rounds are recorded, and `rules`. It runs when a
  card is written, and in the transaction of any void or restore of a round with a card,
  before or after close. A void after close rescores the day, so a voided round's winner
  loses the win and its opponents lose the losses.
- **Closing** sets the status and stops new cards. It does not freeze results against a
  later void.
- **A rescore changes no card's index or handicap.** Every card keeps the index it was
  scored with. A void or restore changes the player's own index from that round on, which
  their next scan uses.

## Records

Career counts, head-to-head and monthly records (tasks 25 and 26) are SQL views over
`daily_results`, `daily_matches` and `daily_card_holes` joined to live rounds, for closed
days only. Months follow the daily's date. Views keep a
rescore from leaving counts stale; a counter table can replace one if it proves slow.

## Before ratings exist

Milestone A ships dailies before Milestone C has ratings runs. Those days have no
`rules`, seed ratings or card handicaps, and no results. They still write cards, which
record who played each day. Those daily rounds count toward their players' indexes once
their seeds are rated at launch (task 12); no other round does.
