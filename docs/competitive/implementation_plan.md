# Competitive play: implementation plan

> **Note**: This document was written by Claude, from a design session with jdharms on
> 2026-11-12. It orders the work that takes [design.md](design.md) to production. Nothing
> in it is built.

Tasks are numbered for reference, not strictly in the order to do them: the
prerequisites and the graph give the order. Every task that adds a page leaves its
English to jdharms (`server/CLAUDE.md`, "Player-facing text").

## Tasks

### Decisions

| # | Task | Needs |
|---|------|-------|
| 1 | Settle the daily's open questions in `design.md` and record them as a proposed ADR. Drafted as ADR 0033: the window, magic words, reruns, bags and the weekly schedule, whose settings live in `schedule.md` | - |
| 2 | Record the standing decisions as proposed ADRs: competition lives on the site; the daily is the unit and there are no seasons; no handicap leaderboard; scores hidden until a player submits, and a closed daily's standing public | - |
| 3 | Record the handicap rules as a proposed ADR once task 9 has set the numbers: last 8 rounds, the allowance, the caps, the provisional period, which rounds count | 9 |

### Ratings engine

| # | Task | Needs |
|---|------|-------|
| 4 | Move the hole model out of `tools/spikes/` into `golf/`: the fit with the spread term, taking rounds and seed holes as plain data, with unit tests on synthetic rounds | - |
| 5 | Estimate the model's variance components and the spread coefficient offline, and store them as a checked-in parameters file the nightly fit reads | 4 |
| 6 | Rate a seed from fitted hole ratings: course rating, slope and stroke indexes, with the baseline fallback for a hole nobody has played | 4 |
| 7 | Compute a handicap index from a player's rounds: the last 8, the net double bogey cap, the low mark and its caps, the provisional state, and which rounds count: daily rounds, plus the rounds a current player's index is seeded from | 4 |
| 8 | Compute a daily handicap and its strokes per hole from an index, a seed's ratings and the allowance, including a player better than scratch | 6, 7 |
| 9 | A maintained backtest (`golf-ratings backtest`) that replays a site database through tasks 6 to 8 day by day, using only what was known each day. Use it to set the numbers, test the caps, the daily-rounds-only rule and a weekly schedule of modes, and set the integrity threshold | 8 |

### Ratings on the site

| # | Task | Needs |
|---|------|-------|
| 10 | Tables for a ratings run, its hole ratings, each player's index history by run, and the current seed ratings (`data_model.md`, Ratings runs) | 4 |
| 11 | The nightly ratings job: read rounds, fit, write a run and every player's index. A systemd timer in `deploy/`, described in `docs/deployment.md` | 5, 7, 10 |
| 12 | Seed each current player's index from all of their recorded rounds on any seed, so the first daily has handicaps. After it, only daily rounds are added | 11 |

### The daily

| # | Task | Needs |
|---|------|-------|
| 13 | The `dailies` and `daily_seeds` tables, and a card for each round on an open daily without its handicap fields (`data_model.md`), the checked-in weekday requests and dated overrides, and the job that generates the day's seed at rollover from its request, with a PRNG seed from the date and a server secret, and the date as its magic words (`design.md`, Magic words), which lowers a manifest word's minimum length to 3 while the word bank keeps 4 to 6. Idempotent, so a missed run catches up | 1 |
| 14 | The daily on the site (`design.md`, On the site): the Daily Seed on the home page, `/daily` and `/daily/<date>`, the calendar of past dailies with each day's theme and player count, and the daily's mark on its seed page. Strings keys for each theme's name and optional description | 13 |
| 15 | Hide a daily's scores from a viewer who has not submitted a round on it: the seed page, other players' round pages and link preview cards | 13 |
| 17 | Admin actions for a daily: replace a broken seed, whose magic words drop the weekday and end in `RERUN` (`NOV 16TH RERUN`), at most once a day and only while the day has no card; withdraw a day, as when some players complete it or a replacement breaks too. Through the audit log | 13 |

### Handicapped results

| # | Task | Needs |
|---|------|-------|
| 18 | At rollover, store the daily's ratings run, its scoring rules, and its seed's course rating, slope and stroke indexes under that run | 6, 11, 13 |
| 19 | When a scan records a round on an open daily, fill its card's index, daily handicap, and net score and Stableford points by hole | 8, 12, 18 |
| 20 | Compute a daily's results under each format: the Stableford standing, every pair's match, and net skins across the field. Formats are separate functions of the field's net scores, so one can be added later. Recomputed when a round arrives, is voided or is restored, before or after close | 19 |
| 21 | Provisional players: shown their own results, left out of the standing, skins and other players' records | 20 |
| 22 | Close a daily: stop new cards. A round received later is an ordinary round; a later void or restore still rescores the day | 20 |
| 16 | When a daily closes, post its standing to Discord by webhook: positions, names, Stableford points and skins, with a link to the daily's page. Show the closed daily's standing on its page to every viewer | 22 |
| 23 | Show results: a player's net card with strokes received, their matches, the standing and the skins, on the round page and the daily's page, only to players who have submitted while it is open (task 16 opens the standing at close) | 15, 20 |
| 24 | Show a signed-in player their handicap for any seed on its page, before they play | 8, 11 |

### Records

| # | Task | Needs |
|---|------|-------|
| 25 | Career counts for each player: dailies played, daily wins, match record, skins won, birdies and eagles, rounds under handicap. Counts only, kept current as dailies close and rounds are voided | 22 |
| 26 | Head-to-head and against-the-field records, for a career and by month | 22 |
| 27 | A player's page: index and low mark with their history, career counts and records. Decide whether it is public | 12, 25, 26 |

### Integrity

| # | Task | Needs |
|---|------|-------|
| 28 | Flag a round far better than the model expected for admin review, at the threshold task 9 sets, using the existing flag and void actions | 9, 11 |

### Launch

| # | Task | Needs |
|---|------|-------|
| 29 | A document page on the site explaining handicaps and the daily's formats; jdharms writes it | 3, 23 |
| 30 | Bring the docs up to date: `server/CLAUDE.md`, the data model and routes in `docs/randomizer_devplan.md`, `docs/README.md`, and the spike's README entry once the model has moved | 23, 27 |
| 31 | Release: migrations on production, the backfill, release notes, the ADRs accepted on merge | 2, 3, 16, 17, 21, 23, 28, 29, 30 |

### Later

| # | Task | Needs |
|---|------|-------|
| 32 | Short opt-in events: several dailies as one tournament, handicaps frozen at its start | 31 |
| 33 | Teams: a daily's field paired for net best ball, or a month between two balanced sides | 31 |

## Milestones

| Milestone | Tasks | What works |
|-----------|-------|------------|
| A. Seed of the day | 1, 13, 14, 15, 17 | The site generates a daily, shows it and hides its scores from players who have not played. No handicaps and no Discord post. This answers the players' request and can ship first |
| B. Ratings proven offline | 4, 5, 6, 7, 8, 9, 3 | Handicaps for every player and every seed can be computed from a database copy, and the numbers are set by backtest |
| C. Ratings live | 10, 11, 12, 24 | Every player has an index that updates nightly, and sees their handicap for any seed |
| D. The handicapped daily | 18, 19, 20, 21, 22, 23, 16 | A daily round produces a net card, a standing, a match against everyone else in the field, and skins, and a closed daily's standing is posted to Discord |
| E. Records | 25, 26, 27 | Career counts, head-to-head and monthly records, and a player's page |
| F. Launch | 2, 28, 29, 30, 31 | Flagging, the explainer, docs and the release |
| G. Events and teams | 32, 33 | Formats layered on the daily |

A and B share no tasks and can go on side by side. C needs B. D needs A and C. E needs
D.

## Dependency graph

```mermaid
flowchart TD
    subgraph A[A. Seed of the day]
        t1[1 Daily decisions]
        t13[13 Dailies table and rollover]
        t14[14 Daily pages]
        t15[15 Hide scores]
        t17[17 Admin actions]
    end
    subgraph B[B. Ratings proven offline]
        t4[4 Hole model in golf/]
        t5[5 Model parameters]
        t6[6 Rate a seed]
        t7[7 Handicap index]
        t8[8 Daily handicap]
        t9[9 Backtest]
        t3[3 Handicap ADR]
    end
    subgraph C[C. Ratings live]
        t10[10 Ratings tables]
        t11[11 Nightly job]
        t12[12 Backfill]
        t24[24 Handicap on seed page]
    end
    subgraph D[D. The handicapped daily]
        t18[18 Rate the daily]
        t19[19 Net score on scan]
        t20[20 Results]
        t21[21 Provisional players]
        t22[22 Close a daily]
        t16[16 Discord standing]
        t23[23 Show results]
    end
    subgraph E[E. Records]
        t25[25 Career counts]
        t26[26 Records]
        t27[27 Player page]
    end
    subgraph F[F. Launch]
        t2[2 Standing ADRs]
        t28[28 Flag rounds]
        t29[29 Explainer page]
        t30[30 Docs]
        t31[31 Release]
    end
    subgraph G[G. Later]
        t32[32 Events]
        t33[33 Teams]
    end

    t1 --> t13
    t13 --> t14 & t15 & t17
    t4 --> t5 & t6 & t7 & t10
    t6 & t7 --> t8
    t8 --> t9
    t9 --> t3
    t5 & t7 & t10 --> t11
    t11 --> t12
    t8 & t11 --> t24
    t6 & t11 & t13 --> t18
    t8 & t12 & t18 --> t19
    t19 --> t20
    t20 --> t21 & t22
    t22 --> t16
    t15 & t20 --> t23
    t22 --> t25 & t26
    t12 & t25 & t26 --> t27
    t9 & t11 --> t28
    t3 & t23 --> t29
    t23 & t27 --> t30
    t2 & t3 & t16 & t17 & t21 & t23 & t28 & t29 & t30 --> t31
    t31 --> t32 & t33
```
