# Competitive play: the weekly schedule

> **Note**: This document was written by Claude, from design sessions with jdharms on
> 2026-11-12 and 2026-11-16. It holds the daily's current settings for each weekday and
> the reasons for them. How the schedule works, and what it may not do, is in
> [design.md](design.md) (The week) and ADR 0033. This table changes when the schedule
> is tuned; git history records each change.

Until task 13 checks the weekday requests in (`implementation_plan.md`), this table is
the schedule. After that the request files are, and this document keeps each day's feel
and the reasons behind it, pointing to the files for the settings.

## The week

Difficulty rises from Monday to Saturday, after the New York Times crossword. Friday and
Sunday step outside the arc.

| Day | Feel | Request |
|-----|------|---------|
| Monday | Easy | standard, expert cap 0, wind speed `gentle`, `vanilla` or `moderate` |
| Tuesday | Easy to medium | natural par, expert cap 1, wind speed `vanilla` or `moderate` |
| Wednesday | Medium | standard, expert cap 1, one storm: `storm_rolling_in`, `storm_passing`, `dying_wind` or `back_nine_pressure` |
| Thursday | Hard | standard, expert cap 1, wind speed `strong` or `back_nine_pressure`, direction `prevailing`, `out_and_back` or `tailwind_out` |
| Friday | Casual Friday | par 3, expert cap 0 or 1, any wind |
| Saturday | Hardest | natural par, expert cap 2 or none, wind speed `strong`, perhaps a 10-club bag |
| Sunday | Sunday Classic | vanilla course, with a wind profile such as a storm; mirror chance 0% or 100%, hazard chance 0%. US ROM only |

Every day but Sunday has a 50% mirror chance and a 50% hazard chance. Thursday's
`tailwind_out` puts the back nine into a headwind, which makes `back_nine_pressure`
fiercer.

## Why

- **Mainstream days.** Standard is the mode players share: since course modes shipped, a
  standard seed has drawn 3.3 rounds, 12 players in all, against 1.7 rounds and 5 players
  for a par 3 seed, most of those from one player. Regulars who have never played a par 3
  or executive course would sit out a day of one, so five of seven days are standard or
  natural par.
- **Saturday is hardest**, as the crossword's is: the arc is prescriptive, not fitted to
  when players play. The field is too small for its busiest day to be stable. Friday's
  par 3 course is short, for a Friday evening.
- **Sunday is special.** The original course is familiar, so the twists are what a
  player notices. It is the one day that needs only the US ROM, the entry point for a
  player without Mario Open. Its holes have the most rounds behind them, so its course
  rating should be the steadiest of the week. If it grows stale it can become one set of
  a rotating Sunday theme: the classic remix, an executive course, a limited bag, a hard
  storm course.
- **Every other day uses both ROMs.** 91 of the 105 seeds on the site need the JP ROM,
  and 13 of the 14 players who have posted a round have played one.
- **Thursday is harder than Wednesday by its wind alone**, at the same expert cap: strong
  wind is the larger of the two levers below. The two wind fields are drawn
  independently, so `back_nine_pressure` meets `tailwind_out` only some weeks, and the
  rest are no easier than before. More ways to separate the days may come later.

## Fairness

The arc matters less to fairness than it looks: slope already scales the strokes a
player receives. It is there for flavor and for gross scores. It has a cost to watch: a
par 3 course's slope (about 0.65) is an extrapolation, so Friday is where a slope error is
likeliest, and a player who plays only Fridays has an index built from par 3 courses
alone. The backtest (task 9) should replay a weekly schedule.

The levers the site's rounds show to move difficulty are the expert cap and wind
strength: strong wind comes out about 2 strokes harder than a player's norm, and no
expert cap about 1.5. Mirror and hazard chances and the ROMs used show nothing beyond
noise, and every effect is confounded with players improving over time. The arc is tuned
once seeds can be rated.
