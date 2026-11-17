# Competitive play: the weekly schedule

> **Note**: This document was written by Claude, from design sessions with jdharms on
> 2026-11-12 and 2026-11-16. It holds each weekday's feel and the reasons for its
> settings. How the schedule works, and what it may not do, is in
> [design.md](design.md) (The week) and ADR 0033.

The settings themselves are the requests in `golf/randomizer/data/daily_schedule.json`,
one per weekday and one per dated override, each with the theme that names the day on
the site: a name and an optional one-line description, stored with the day when it is
made. That file is the schedule; a change to it is tuned there and recorded in git
history, and `golf/randomizer/daily.py` checks it.

## The week

Difficulty rises from Monday to Saturday, after the New York Times crossword. Friday and
Sunday step outside the arc.

| Day | Feel | What sets it apart |
|-----|------|--------------------|
| Monday | Easy | Natural par, no expert holes, light to moderate wind |
| Tuesday | Easy to medium | Standard, one expert hole a nine, vanilla or moderate wind |
| Wednesday | Medium | Standard, one expert hole a nine, a storm |
| Thursday | Hard | Standard, one expert hole a nine, strong wind or back-nine pressure, with a direction profile, a bag of at most 10 clubs |
| Friday | Casual Friday | Par 3s, derived holes included, any wind |
| Saturday | Hardest | Natural par, two expert holes a nine or no cap, strong wind |
| Sunday | Sunday Classic | A vanilla US ROM course with a storm; mirrored whole or not at all. US ROM only |

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
- **Thursday is harder than Wednesday by its wind and its bag**, at the same expert cap:
  strong wind is the larger of the two levers below, and a 10-club bag leaves gaps a
  player has to play around. The two wind fields are drawn independently, so
  `back_nine_pressure` meets `tailwind_out` only some weeks, and the rest are no easier
  than before. More ways to separate the days may come later.

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
