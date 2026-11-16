# Competitive play: data model

> **Note**: This document was written by Claude, from a design session with jdharms on
> 2026-11-16. It is the proposed storage for [design.md](design.md): what a daily, its
> ratings and its results keep, and when each is fixed. Nothing in it is built. Column
> lists are the shape, not the final migration.

## The rule

Every result is reproducible from rows that were written once and never changed. What a
daily depends on keeps changing: hole ratings and player indexes are refit nightly, the
rules' numbers are tuned after the backtest, and the weekday requests and rollover hour
may change. A daily therefore points at an immutable ratings run and copies everything
else it uses at three moments:

| Moment | What is fixed | Tables |
|--------|---------------|--------|
| Open | The day's window, request, seed, ratings run, the seed's ratings, the scoring rules | `dailies`, `daily_seeds`, `daily_seed_holes` |
| Scan | A player's index, handicap and net card | `daily_cards`, `daily_card_holes` |
| Close, and any later void or restore | The standing, matches and skins | `daily_results`, `daily_matches` |

This follows ADR 0024, where a seed is what the site stored rather than a recipe to
rebuild: a daily is what was stored when it opened, when each round arrived and when it
was scored, not something the current model and rules recompute.

Not fixed: Discord names and avatars. Records follow a player under their current name,
as every other page does.

## Ratings runs

The nightly job (task 11) writes a new run and never edits an earlier one.

| Table | Holds |
|-------|-------|
| `ratings_runs` | id, created_at, the model version, the parameters file's hash and a JSON copy of its values, the fitted baseline coefficients (par, distance, wind), the index rules used (window, hole cap, low-mark caps, provisional threshold), the latest `received_at` the fit saw, the number of rounds it used |
| `hole_ratings` | run, hole id, data hash, pin index: the fitted layout, terrain and pin effects combined into the hole's rating and spread factor. Only levels the run saw rounds for; any other hole is rated from the run's baseline coefficients |
| `player_indexes` | run, user: handicap index, low mark, rounds counted, provisional. One row per player the run could rate. This is the index history: the runs are nightly, so a player's rows by run are their index by day |
| `seed_ratings_current` | seed, position: rating, spread factor, stroke index, under the latest run only, with the seed's course rating and slope. Replaced each night. It serves a player's handicap for any seed (task 24); a daily copies its own instead |

The hole keys are the spike model's (`tools/spikes/player_skill.md`): `hole_id`,
`data_hash` and the pin index, all on `seed_holes`.

Every run is kept, hole ratings included: about a thousand rows a night at the 2026-11
catalog size, a few megabytes a year. A daily copies its own seed's ratings, so nothing
depends on an old run's hole ratings, but they show how a hole's rating moved and let a
backtest or a question about an old day be answered from the database.

## Dailies

| Table | Holds |
|-------|-------|
| `dailies` | date (primary key: the daily's own date, `YYYY-MM-DD`), the schedule key it came from (a weekday or a dated override), the request as it was read (ADR 0016), `opens_at` and `closes_at` in UTC, status (`open`, `closed`, `withdrawn`), `ratings_run_id`, `rules`, closed_at |
| `daily_seeds` | date, attempt (1, or 2 for a rerun), seed (unique), replaced_at. The day's seed is its latest attempt. A seed's pages know it is a daily, and hide its scores, from this table |
| `daily_seed_holes` | seed, position: rating, spread factor and stroke index under the daily's run; course rating and slope on `daily_seeds` |

- **The request is copied** because the checked-in weekday requests change, and a past
  day keeps the request it was drawn from. The concrete settings are already in the
  seed's manifest.
- **The window is stored per day**, so a later change to the rollover hour or the
  window's length leaves past days as they ran. Windows overlap by an hour (design.md,
  The window); the Daily Seed is the open one with the latest date.
- **`rules`** is a JSON copy of the scoring numbers in force at open: the allowance, the
  Stableford table and the formats scored. The index rules belong to the ratings run, not
  to the daily.
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

A card is written when the server accepts a round on the day's current seed while the
day is open. Its existence is what makes a round a daily round.

| Table | Holds |
|-------|-------|
| `daily_cards` | date, user (unique together), the round's `public_id`, the index, low mark and provisional flag from the daily's run, the daily handicap, created_at |
| `daily_card_holes` | date, user, position: handicap strokes received, net score, Stableford points |

- **The round is referenced by `public_id`**, which a void and restore keep, not by row
  id, which they change.
- **A card is never deleted.** It counts while its round is in `rounds`; a voided round's
  card is ignored until the round is restored.
- **A round after close** is an ordinary round on the seed and gets no card, as does a
  round on a withdrawn day.
- **The nightly fit reads daily rounds from cards** on days not withdrawn, so a change to a day's window never
  changes which rounds counted toward an index.
- **A provisional player gets a card.** The flag keeps them out of others' standing,
  skins and records.

The values on a card could be computed again from the run, the rules and the round. They
are stored because they are what the player was shown that day, and so records are plain
SQL over rows.

## Results

| Table | Holds |
|-------|-------|
| `daily_results` | date, user: Stableford points, position among the counting players (ties shared; NULL for a provisional player), skins won, whether they won the day, whether the row counts (not provisional) |
| `daily_matches` | date, the two users: holes won by each, the result (win, half, loss) and its margin. Every pair of cards, provisional included, so a provisional player sees their own matches |

- **Scoring is a pure function** of the day's counting cards and `rules`. It runs when a
  card is written, and in the transaction of any void or restore of a round with a card,
  before or after close. A void after close rescores the day, so a voided round's winner
  loses the win and its opponents lose the losses.
- **Closing** sets the status and stops new cards. It does not freeze results against a
  later void.
- **A rescore changes no handicap or index.** Other players' cards keep the indexes they
  had at open; the next nightly fit sees the void.

## Records

Career counts, head-to-head and monthly records (tasks 25 and 26) are SQL views over
`daily_results`, `daily_matches` and `daily_card_holes` joined to live rounds, for closed
days only, with both players counting. Months follow the daily's date. Views keep a
rescore from leaving counts stale; a counter table can replace one if it proves slow.

## Before ratings exist

Milestone A ships dailies before Milestone C has ratings runs. Those days have no
`ratings_run_id`, `rules`, seed ratings or card handicaps, and no results. They still
write cards, which record who played each day. Task 12 seeds each player's first index
from every round recorded up to then, A's daily rounds among them, and from then on only
cards are added.

## Open questions

| Question | Leaning |
|----------|---------|
| Whether `seed_ratings_current` is a table or computed on request | A table, rewritten nightly; a request should not run the model |
