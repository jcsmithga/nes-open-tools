# Competitive play: design

> **Note**: This document was written by Claude, from design sessions with jdharms on
> 2026-11-12 and 2026-11-17 (ratings and handicaps). It records the problems competitive play has to solve and the current
> proposal for each. The ordered work is in
> [implementation_plan.md](implementation_plan.md).

## Goal

A more interactive way to play the randomizer than comparing scorecards on a seed's page
after a round. Players compete against each other across skill levels, at different
times and in different places, on the site itself.

The pieces already exist. Every player on a seed faces the same holes, pins and wind. A
round comes back to the site through the scorecard QR code. The hole scores the site has
collected are enough to rate players and holes (`tools/spikes/player_skill.md`).

## What a proposal must satisfy

Every format here was judged against these, and a new one should be too.

1. **Skill levels compete.** Ratings on the site run from 7 under par to 42 over. Without
   a handicap the stronger of two players wins 88% of the time, and 98% once the gap is
   8 strokes or more.
2. **No reason to sandbag.** Playing badly on purpose must never pay.
3. **No reason not to play.** A round played must never leave a player worse off than a
   round skipped. Nobody is eliminated, and nobody is so far behind that catching up
   feels impossible.
4. **Golf conventions where they fit.** Handicap index, course and slope rating, stroke
   index, match play, Stableford: a golfer should recognize them, and they give flavor
   to everyone else.
5. **No organizing.** The most common request from players is a seed of the day. We read
   it as "I want to play against others, and I do not want to arrange it."
6. **Collect little.** No new data about a player unless a feature needs it.

Players are asked how a feature feels once it exists, not asked to design it.

## Primitives

| Term | Meaning |
|------|---------|
| Daily | The seed of the day: one seed the site generates each day, open to every signed-in player for a fixed window. The unit of competition |
| Reference holes | The 54 untransformed NES Open holes, each hole's four pins averaged, with no wind. Index 0 and slope 1.0 are measured on them |
| Hole rating | For one hole as built (layout, terrain, pin, wind): the strokes an index-0 player is expected to take, and its spread factor, how much it separates players of different skill |
| Course rating | The sum of a seed's 18 hole ratings: what an index-0 player is expected to shoot |
| Slope | The mean of a seed's 18 spread factors. 1.0 separates players as much as the reference holes do |
| Stroke index | A seed's holes ranked 1 to 18 by difficulty for their par, the order handicap strokes are given in |
| Handicap index | A player's expected score over par on the reference holes, per 18 holes, from their recent rounds |
| Starting index | The fixed index every player starts from, 0. It counts as 4 rounds and fades as the player's own rounds arrive |
| Low mark | The lowest handicap index a player has held in a trailing window |
| Daily handicap | The whole number of strokes a player receives on one seed: index, slope, course rating and the allowance combined |
| Allowance | The share of a handicap a player receives. Below 100% it favors the stronger player |
| Net score | Strokes on a hole less the handicap strokes received there |
| Format | One way of scoring a daily's field. A daily is scored under several at once |
| Result | What a format gives a player for one daily: a standing, a match against each other player, skins won |
| Record | A count of results over time: career, by month, and head to head |

## Architecture

Competitive play is part of the randomizer site. The site already holds identity, entries,
rounds and the QR loop, so results need no scraping and no second system. A player who
finds the site finds the competition, with no chain of Discord servers to follow.

A Discord bot or a separate site on top of an API is how other randomizers do it, mostly
because their results cannot reach the randomizer. Ours can. Discord's part here, after
launch, is an announcement when a daily closes, carrying its standing. A daily is always open, so its
opening is not news; its result is.

Ratings are computed in three places, none of them a scheduled job (jdharms,
2026-11-17):

- **The model's parameters** are estimated offline, with all the CPU they need, and
  checked in (Fitting).
- **The hole ratings** are refitted once a day, at the daily's rollover, in the app's
  own loop that already makes the daily. Under the checked-in parameters the fit takes
  a second or two. Each seed is rated from the latest fit when it is generated, and
  keeps that rating.
- **A player's index** is updated when one of their daily rounds is scanned: the round
  is scored with the index they hold, and their new index is computed from their
  counted rounds and their seeds' stored ratings. Nothing else changes an index, so
  nothing needs to run in between.

## The daily

**Problem.** Players want to compete without arranging it, and results are only
comparable when players share a seed.

**Proposal.** The site generates one seed each day. Everyone who submits a round on it
while it is open is in that day's field. A player's round is scored against the whole
field: a standing, and a match against every other player in it. Nothing is scheduled and
nobody is paired. The next day starts from nothing.

The daily is the competition. Records, events and teams are built on top of it, and none
of them changes what a daily is.

The decisions in this section are recorded as ADR 0033 (proposed).

- A daily is an ordinary seed. Once it closes, rounds on it are recorded as on any seed
  and count toward a player's index and career counts, but not toward that daily.
- The first submission is a player's round, as on every seed.
- Players choose their own bag, as on every seed, unless the day's request sets club
  rules.
- Its course rating, slope and stroke indexes are fixed when it is generated, as every
  seed's are.
- A player's round on it is scored with the index they hold when it is scanned: their
  index after their previous counted round. With one daily a day that is the index they
  held when this daily opened. The one exception is the hour two dailies overlap: a
  player who scans the older daily in that hour carries its update into the newer one,
  so their round on the newer one is scored with an index that already counts the
  older.
- Its results are scored as rounds arrive, and scored again whenever a round on it is
  voided or restored, before or after it closes.
- What it stores, and when each part is fixed, is in [data_model.md](data_model.md).

### The window

**Decision** (jdharms, 2026-11-16). A daily opens at 09:00 UTC and stays open 25 hours,
closing at 10:00 UTC the next day.

- **09:00 UTC is the quietest hour.** In the site's rounds from 2026-09-20 to 2026-11-16,
  every player plays from North America. The latest rounds arrive about 05:00 to 06:00
  UTC (9 to 10 PM Pacific) and the earliest about 11:00 UTC (6 AM Eastern); from two
  hours before 09:00 to an hour after, there were no rounds and no downloads. It is 4 AM
  Eastern and 1 AM Pacific in winter, an hour later under daylight saving.
- **The extra hour is a grace period** for a player who downloads just before the next
  daily opens. A round arrives a median 42 minutes after the player's first download,
  and 14 at the fastest.
- **For that hour two dailies are open.** The site always shows the latest as the Daily
  Seed. The older one is still played and scored, and is reached from a tab already
  open, a link, or the calendar of past dailies.

### On the site

**Decision** (jdharms, 2026-11-16).

- **The home page** shows the Daily Seed, the latest open daily, titled by its theme,
  with its date, how long until it closes, how many players have played it, and a link
  to its seed page. A signed-in player who has played it also sees their own round's
  score, linking to the round: nothing is spoiled for them.
- **`/daily`** redirects to the Daily Seed's page, a link that never goes stale.
  **`/daily/<date>`** redirects to that day's seed, the replacement after a rerun.
- **The calendar of past dailies** shows each day's theme and how many players it drew,
  which shows activity without spoiling a score, and links to `/daily/<date>`.
- **A daily's seed page** says it is a daily, with its date and theme, and links to the
  calendar, so a link shared on its own still says what it is.
- **A theme is a name and an optional one-line description**, written in the schedule
  beside each request and stored with the day when it is made, so renaming a weekday's
  theme leaves past days alone. Weekday themes may need only a name; the description is
  for special dailies, such as a quarterly major or another event set by a dated
  override, which names itself.
- **Signed-out players** may download a daily as a guest ROM, as any seed (ADR 0003). The
  seed page's guest marker says it cannot submit, and its scores are hidden from them as
  from any viewer who has not submitted.
- **The host page** lists a daily among the recent seeds like any other, so a host can
  run it at an in-person event; rounds recorded in the window are daily rounds.
- **A daily round's page** shows its net card under the gross scorecard: the index and
  handicap it was scored with, the strokes received on each hole by stroke index, the net
  scores and the Stableford points, subject to Spoilers below. `/me` shows the player's
  handicap index.
- **A daily's seed page** shows its results under its banner: the standing beside the
  viewer's own matches, best result first, and each hole's skins in a collapsed list,
  subject to Spoilers below.

Until handicaps exist (Milestone A), a daily shows its recorded rounds as any seed does,
to players who have submitted: no standing, no winner and no records. A winner by gross
score would teach players a competition that handicaps then take away.

### Magic words

**Problem.** Magic words let a player check they loaded the right ROM. With a daily, the
likely mistake is yesterday's ROM, and three random words do not show that.

**Decision** (jdharms, 2026-11-16). A daily's magic words are its date: the weekday, the
month and the day, in US order, such as `MON NOV 16TH`.

- The date is the daily's own, not the calendar date anywhere on the globe when it
  opened.
- Weekdays are `MON`, `TUES`, `WED`, `THURS`, `FRI`, `SAT` and `SUN`; months are the
  three-letter abbreviations; the day is an ordinal (`1ST`, `2ND`, `3RD`, `16TH`).
- No year. The weekday ties the seed to the week's schedule, and nobody confuses today's
  ROM with the same date a year ago.
- A replacement for a broken daily is `NOV 16TH RERUN`, so a player holding the broken
  ROM sees a mismatch. A rerun is only for a seed nobody can complete, and a daily is
  replaced at most once: if some players complete it, or the replacement breaks too, the
  day is withdrawn ([data_model.md](data_model.md)).
- Every other draw is unchanged: magic words have their own stream of the PRNG seed, so
  the daily job sets them without moving the holes, wind or music.

`MON` and `NOV` are 3 characters, below the word bank's 4 to 6. The menu pads every word
to a 6-character slot and the scorecard title has 26 columns, so the fonts and layout
allow 3. A manifest's words may be 3 to 6 characters while the word bank keeps 4 to 6.
`TUES NOV 17TH` was checked in an emulator on 2026-11-17 and draws correctly.

### The week

**Problem.** A daily needs settings, and players differ in which modes they play. Fields
will be small: about 10 players were active in the two weeks before 2026-11-12, and the
whole site records about 4 rounds a day, so a daily is likely to draw 3 to 6. Every
unusual mode splits that field further.

**Decision** (jdharms, 2026-11-16). Each weekday has its own request (ADR 0016). The
current requests, and the reasons for each, are in [schedule.md](schedule.md); they are
tuned there without changing what follows.

- **Difficulty rises from Monday to Saturday**, after the New York Times crossword, as a
  prescription rather than a fit to when players play. Friday and Sunday step outside
  the arc.
- **Most days are mainstream.** Standard and natural par are the modes players share, so
  they fill five of seven days.
- **Sunday needs only the US ROM**, the entry point for a player without Mario Open.
  Every other day may use both.
- **Requests are checked in**, one per weekday, with a table of dated overrides for
  events, holidays or a day of newly published holes. A schedule change is in git
  history. Weighted fields vary a weekday from one week to the next.
- **The PRNG seed comes from the date and a server secret.** The date makes generation
  idempotent, so a missed run regenerates the same seed. The requests are public in the
  repository, so the secret is what stops anyone with the CLI from generating a daily
  ahead of time and practicing it.
- **Seeds are not gated on their rating.** Generating until a course rating falls in a
  day's band would change the odds the request states. Each daily's course rating and
  slope are stored and shown, and after a month or so they say whether Thursday plays
  harder than Monday.
- **The site publishes the weekly themes, not the settings.** Players can plan around
  "Friday is par 3s", and see the day's wind and expert cap once its daily opens. The
  settings themselves are in the repository for anyone who looks.

## Handicaps

The decisions in this section are recorded as ADR 0034 (proposed).

### Rating holes and courses

**Problem.** A generated course has never been played, and seeds range from par 54 short
courses to hard full ones.

**Proposal.** The hole model of `tools/spikes/player_skill.md` already rates holes: par,
distance and wind give a baseline, and every round played on a layout, a terrain variant
or a pin, in any seed, refines it. A generated course is rated the moment it exists,
because nearly all of its holes have been played elsewhere. A hole nobody has played
falls back on its baseline.

A seed's ratings are stored when it is generated and never redone (jdharms,
2026-11-17). Every round on it is scored against them, so a round's differential never
changes and a player's index moves only when they play. Within a daily nothing would be
gained by re-rating: every player shares the stored rating.

**Mirrored holes** (jdharms, 2026-11-17). A hole that is mirrored and otherwise unchanged
is the same hole as its unmirrored twin: it plays the same, flipped, so its rounds rate
one hole and its pins. On the NES Open holes that adds 329 hole scores to the 1,318
untransformed ones. A site-wide mirrored term measures any effect of the flip itself,
such as a player's expectations of the hole, and is dropped if it cannot be told from
zero. A hole with redrawn hazards stays its own variant, mirrored or not, keyed by the
hole it built. Redraws often coincide even so: on 2026-11-17, 709 redrawn slots built
532 distinct holes, 45 of them the hole with no redraw, since a hole with few hazards
has few shapes to roll.

On the 2026-11-17 database it cannot: compared within the 124 rounds that have both
kinds, where the player, the day and their form cancel, a mirror-only hole plays
-0.04 +/- 0.08 strokes a hole against its twin (p = 0.65), so the fit has no mirrored
term. Fitted as a term, it comes out at -0.18 +/- 0.05, but that error treats every hole
as independent: the rounds with mirrored holes were simply better rounds, played after
mirroring arrived a month into the site, and the fit's one skill per player cannot see
a player's form. `golf-ratings estimate` reports the paired test each time.

The model gains one term. The gap between two players is wider on harder holes: a hole
one expected stroke harder separates players by about 44% more (gamma, 0.443 on the
2026-11-17 database). Each hole therefore has a spread factor, and the model is

```
strokes = rating(hole) + spread(hole) * index(player) / 18 + luck
spread(hole) = 1 + gamma * (rating(hole) - mean par of the reference holes)
```

where a hole's rating is what an index-0 player is expected to take on it (The
reference). Summed over 18 holes this is golf's own pair of ratings: the course rating is
the sum of the hole ratings, and slope is the mean spread factor. From the 2026-11-12 fit,
measured against the field rather than the reference holes, a par 54 short course has a
slope near 0.65 and a hard full course near 1.25. The model is `golf/ratings/model.py`.

An error in a course rating moves every player's handicap equally, so it cancels in every
comparison between players and shows only in scores against one's own handicap. An error
in slope does not cancel.

### The reference

**Problem.** The model cannot tell skill from difficulty on its own: a stroke added to
every player and taken from every hole predicts the same scores. The spike settles it by
pulling skills toward the field's average, which makes index 0 the average player on the
site. Every index and course rating would then move as players join, leave and improve:
the spike's offset to par moved from +7.5 to +10.4 between 2026-10-19 and 2026-11-10 as
players joined.

**Decision** (jdharms, 2026-11-17). Ratings are measured against the reference holes: the
54 untransformed NES Open holes of the US, UK and Japan courses, each hole's four pins
averaged, with no wind.

- **Index 0** is a player expected to shoot par over the reference holes. A player's
  index is their predicted score over par there, per 18 holes. A prediction does not
  depend on how the model splits skill from difficulty, so the index does not move with
  the field.
- **The spread term is centered on the reference holes**, so their average spread factor
  is 1.0: a seed of slope 1.0 separates players as much as they do. It is golf's slope
  of 113, written as 1.0. The site shows it on golf's scale, 113 times the slope rounded
  to a whole number; every formula uses the model's.
- **A seed's course rating** is an index-0 player's expected score on it. Then
  `index * slope + course rating - par` is the model's expected score over par for that
  player.
- **The reference holes' ratings are refined** as rounds come in, so an index can move a
  little without its player's play changing. That is new information about the holes,
  not a change in the field.
- **The Mario Open holes are left out**, and share no hole with the reference. The five
  Mario Open courses' ratings are a check on plausibility, not on the anchor, since their
  holes are still in the fit.
- **Sunday Classic is the direct check.** It plays a vanilla US course, so on an
  unmirrored Sunday a +10 player should average about 10 over par, once the model's wind
  term accounts for the storm.

### Fitting

**Decision** (jdharms, 2026-11-17).

- **The model's own numbers are estimated offline**: how much players, layouts, terrains
  and pins each vary, which sets how far a thin rating is pulled toward its group, and
  gamma and the mirrored term. They are checked in as a parameters file, and re-estimated
  by hand as the data grows, about quarterly. The estimate takes all the CPU time it
  needs. The daily refit on the web server (Architecture) reads the file and only solves
  for the ratings, which takes seconds, and its run records the file's hash.
- **The estimate runs to convergence**, every parameter estimated together, by
  expectation-maximization (EM). The 2026-11-10 fit stopped at its 500-iteration cap, and
  the backtest's gamma was fitted on penalties from a fit without it.
- **It reports how certain gamma is**, by refitting on players resampled with
  replacement. On the 2026-11-17 database gamma is 0.443, and across 100 resampled
  rosters 90% fall between 0.35 and the search's ceiling of 0.95: it is the soft number.
  The backtest's fairness moves with it: at the steady state the stronger player takes
  58% of net matches at 0.30, 55% at 0.443 and 49% at 0.60. The first two are within the
  noise; past about 0.6 the allowance would need revisiting.
- **Players improving.** The fit gives each player one skill for all time, so an
  improving player's early rounds make the holes they played look harder. Before choosing
  a model, the single skill is compared with a skill per player per month, or one that
  drifts, by how much the course ratings of real seeds move: a few tenths of a stroke
  keeps the single skill, a stroke or more adopts the other. On the 2026-11-17 database
  a month's skill varies 0.13 strokes a hole about the player's and moves live seeds'
  course ratings 0.19 on average and 0.80 at most, so the single skill stays. Decaying
  old rounds, as college basketball ratings do, is turned down for hole ratings: a hole
  does not change, so an old round says as much about it as a new one. Recency belongs
  in the index.
- **Slope follows from course rating.** With one gamma for every hole, a seed's slope is
  `1 + gamma * (course rating - 72) / 18`, so two seeds of the same rating always have
  the same slope. Real golf measures slope apart from course rating, by rating a course
  for a scratch and a bogey golfer, because trouble at short range, such as a forced
  carry, hurts the weaker player far more than length does. Hole scores can show that
  directly: a spread for each layout beyond what its rating predicts is tested at the
  next re-estimate (implementation task 34).

### The handicap index

**Problem.** Players improve. Against an index built from all of a player's rounds,
improving players beat their number by 2 to 7 strokes a round while the strongest player,
who has plateaued, beats his by 0.1. He then loses about 70% of his net matches.

**Decision** (jdharms, 2026-11-17). The index is the mean of the best 8 of a player's
last 10 differentials, blended with the starting index while it lasts (New players). A
record shorter than 10 keeps the same share, 80% of its rounds.

- **It follows improvement**, as the last 8 did on the spike's backtest, where the
  last 8 removed the link between skill and net result (correlation +0.01 in net stroke
  play).
- **It all but ends sandbagging.** A thrown round is usually one of the two dropped: a
  round thrown by 15 strokes at a player's 25th round buys 4 strokes of index summed
  over the next 8 rounds, against 19 with the last 8 (Sandbagging).
- **It stops a volatile player's blowups from inflating their index**, so their good
  days do not take dailies from a consistent player on strokes their bad days earned.
- **An index reads optimistic**, as golf's does: it is what a player shoots on a good
  day. On the 2026-11-17 backtest a round averages 2.2 strokes worse than its index
  predicts, new players' rounds included. Matches do not care, since the allowance is
  set on the result; the integrity flag allows for it.

Turned down:

- **All rounds.** Lags improvement, as above.
- **The last 8.** Fair at the steady state, but a thrown round counts in full for 8
  rounds, and a volatile player's blowups raise their index.
- **Best 8 of the last 20**, the World Handicap System's rule. It drops 12 of 20 rounds,
  which punishes erratic players, whose best rounds sit far from their usual ones: on the
  spike's backtest one +9 player fell to 0.26 match points.

### The daily handicap

**Problem.** A handicap has to be legible. A fractional adjustment on every hole is not.

**Proposal.** Golf's formula, giving a whole number of strokes for the day:

```
daily handicap = round(allowance * (index * slope + course rating - par))
```

Strokes are given in stroke index order: one on each hole from stroke index 1 until they
run out, a second lap past 18. A player better than scratch gives strokes back, starting
from stroke index 18. In a match, the weaker player receives the difference between the
two daily handicaps.

A fractional "personal par" for each hole was tested and was no fairer.

**Stroke index** (jdharms, 2026-11-17). A seed's holes are ranked by difficulty for their
par: the hole's rating less its par, ties broken by spread factor. Ranking by spread
factor would put strokes where the gap between players is widest, but spread rises with
total strokes, so par 5s would nearly always take indexes 1 to 4, which reads oddly. The
daily handicap is the same either way; only hole-by-hole formats feel the order, and the
backtest reports what it costs in fairness against ranking by spread.

### The allowance

**Problem.** A full handicap favors the weaker player. Weaker players are still improving,
and their scores vary far more: net scores spread about 3 strokes for the strongest
player and about 8 for players near +9.

**Decision** (jdharms, 2026-11-17). The allowance aims at a target: the stronger of two
players takes about 55% of their net matches, a slight edge for skill. At 50%, a
handicap's textbook goal, improving lowers a player's index without winning them more
matches. The World Handicap System sits above 50% too: its 95% exists partly because
higher handicaps' scores vary more, which in a field wins them more than their average
deserves, and the daily's standing is a field.

The allowance is a round number that meets the target, measured at the steady state,
between players who both have a full record of 10 rounds: 90% on the 2026-11-17 backtest
(the stronger player takes 55% at 90%, 52% at 95% and 49% at 100%). It is not solved for
at each re-estimate. It changes only if the target changes, or if the stronger player's
share drifts from it: `golf-ratings backtest` reports the share's range across resampled
rosters and says when it no longer holds 55%. On 2026-11-17 that range is 45% to 63%, so
only a large drift shows until rounds accumulate.

The spike's numbers on the 2026-11-12 database:

| Allowance | Stronger player's match score | Strongest player's match points |
|-----------|-------------------------------|---------------------------------|
| 100% | 48% | 0.37 |
| 90% | 55% | 0.49 |
| 80% | 61% | 0.59 |
| 70% | 66% | 0.67 |

At 90% the link between skill and results is zero. Below about 85% the stronger player
has a real edge, and it comes from the +9 group, who fall to a third of the points at
80%. As improvement slows the fair value should rise; the World Handicap System uses 95%
for individual stroke play.

### New players

**Problem.** One round says little. A rating is about 6 strokes off after one round and 2
to 3.5 after two or three. A provisional period, whose rounds feed the index but count
in no result, makes those rounds free to throw: a few bad ones inflate the first index,
and the player cashes it in later.

**Decision** (jdharms, 2026-11-17). No provisional period. Every player starts at
scratch, a starting index of 0, which counts as 4 rounds in their index, half the
window. It fades as their own rounds fill the window, by half a round with each, so it is
gone once they have 8 and the index never jumps.

| | After round 1 | After round 4 | After round 8 |
|---|---|---|---|
| A +20 player, playing honestly | +4.4 | +12.0 | +20 |
| A -10 player, playing honestly | -2.7 | -6.8 | -10.7, back to -10 as the cut rounds age out |
| A scratch player who throws round 1 at +35 | +7.8 | -0.2 | -0.2: by round 3 the thrown round is the one dropped |

The -10 player's index falls faster than the starting index alone would take it, since
each of their first rounds is 10 strokes better than the index they held, an exceptional
score (Sandbagging).

- **Every player is in every result** from their first daily: the standing, the matches
  and the skins. Fields of 3 to 6 cannot spare anyone.
- **Scratch is chosen for being simple** and for erring against the newcomer. A strong
  player new to the site, or a strong player's second account, should not take dailies
  on strokes a starting index gave them; a weak newcomer receives fewer strokes than they
  need for their first several dailies, softened by their index rising after each one
  (jdharms). On the 2026-11-17 backtest a new player's round averages 4.6 strokes worse
  than their index predicts while the starting index lasts.
- **A thrown early round costs that day's result and buys little**: dropped by best 8 of
  10 and diluted by the starting index, a round thrown by 15 strokes as a player's first
  buys 10 strokes of index over the next 8 rounds, against 22 with no starting index.
- **Starting values of +4.5 and +9, and a weight of 2 rounds, predict new players' rounds
  better**, +4.5 and a weight of 2 clearly. They are turned down for the reason above:
  the starting index is there to keep strong newcomers from cleaning up, not to predict
  the typical one.

No scheme closes the hole while early rounds feed the index; golf accepts it too.

### Setting the numbers

**Decision** (jdharms, 2026-11-17). The backtest (implementation task 9) starts every
number from the World Handicap System's value, and changes one only when another value
wins clearly: in nearly all of many rosters resampled from the site's players with
replacement. The allowance is the exception: it is set by a target (The allowance), and
every comparison measures fairness against that target. Its pairings come from few players, three of them in most, so a few points
between two settings is noise, and a number kept for no clear reason should be the one a
golfer recognizes.

| Number | World Handicap System | Here |
|--------|-----------------------|------|
| Rounds in the index | Best 8 of the last 20 | Best 8 of the last 10 (The handicap index) |
| Allowance | 95% in individual stroke play | 90%, for a target (The allowance) |
| Hole cap for the index | Net double bogey | The same |
| Exceptional score reduction | A differential 7.0 better than the index takes 1.0 off the record, 10.0 better takes 2.0 | The same |
| Low-mark caps | A rise past 3 counts half; never more than 5 | The same |
| Low mark's window | 365 days | The same |
| Rounds before a low mark exists | 20 | The same |
| New players | An index after 54 holes | Starting index 0, worth 4 rounds (New players) |

Fairness is measured at the steady state, between players who both have a full record
of 10 rounds (jdharms): the rules are for a league of regulars, and new players' early
rounds are the starting index's business. On the 2026-11-17 database (`golf-ratings
backtest`: 262 rounds replayed, 18 players, 1,000 rosters, 153 steady-state pairs) the
stronger player takes 55% of their net matches; without handicaps, 87%. No alternative
brings that share clearly nearer the target: the last 8, best 8 of 20, no exceptional
score reduction, no caps, a low mark after 8 rounds, stroke indexes by spread, other
starting values and weights, or another allowance. In all pairs, new players' early
rounds included, it is 65%: the price of a starting index that errs against the
newcomer. The numbers live in `golf/ratings/data/parameters.json`.

## Formats

**Problem.** A format has to survive a blowup hole (6.3% of holes are 3 over par or
worse) and a field of two.

**Proposal.** One round is scored under several formats at once. A format is a way of
reading the same field's net scores, so adding one costs a player nothing and asks
nothing of them. Each daily produces three results for each player.

- **Standing**, by net Stableford points: 2 for a net par, 1 more for each stroke under,
  1 fewer for each stroke over, never below 0. A blowup costs one hole's points, and
  Stableford narrows the spread of erratic players from about 8 strokes to about 6. The
  standing decides the day's winner, in a field of at least 2. Ties at every position
  are broken by countback: the most points over the last 9 holes, then the last 6, the
  last 3 and the 18th; players still tied share the position (jdharms, 2026-11-17).
- **Matches**, net match play against every other player in the field, hole by hole, over
  all 18 holes even once a match is decided.
- **Skins**, net, across the whole field, in a field of at least 2: a hole is won by the
  one player with the lowest net score on it. When that score is shared, the skin carries
  over, and the next hole won takes every skin carried to it (jdharms, 2026-11-17: more
  drama). Skins still carried after the 18th are won by nobody, for now: a day stores
  its rules when it opens, so a change applies only to days opened after it. Skins are for fun. They are a game of variance, not a fair contest: on the
  backtest, without carryover, the strongest player wins 0.3 of a fair share with
  handicaps, and everyone else wins 0.15 to 0.3 of theirs without; carryover adds
  variance. Skins won are a career count and decide nothing else.

Turned down: gross play of any kind as the main competition, by constraint 1.

## Incentives

### Sandbagging

**Problem.** A handicap built from recent rounds can be raised by playing badly. With the
last 8 rounds, one round thrown by 15 strokes moves an index about 2 strokes.

**Decision** (jdharms, 2026-11-17). Golf's own rules:

- **Every result counts.** No format drops a player's worst days, so a thrown daily costs
  that day's standing and every match in it.
- **A cap on each hole.** For the index, a hole counts no worse than a net double bogey.
- **A cap above the low mark.** Past 3 strokes over a player's low mark, a rise in index
  counts half; it never exceeds 5 over. The numbers are the World Handicap System's and
  need tuning for a field that is still improving. A low mark exists once a player has
  20 rounds.
- **Every round that counts toward an index counts in results** (New players), so no
  round is free to throw.
- **Exceptional score reduction.** A round 7 or more strokes better than the player's
  index takes 1 stroke off every differential in their record, 10 or more takes 2, so
  an index catches a player who is better than it says at once, and the cut fades as
  those rounds age out. Its main use here is a strong player new to the site.
- **Best 8 of 10** (The handicap index): a thrown round is usually one of the two
  dropped.

On the 2026-11-17 backtest, one round thrown by 15 strokes raises the index by 4 to 10
strokes summed over the next 8 rounds, far fewer handicap strokes than it threw away,
and it costs that day's result. With the last 8 it bought 13 to 19. The low-mark caps
barely change it, since they need 20 rounds; they change 12 of the 102 indexes that have
a low mark, honest players' slumps included.

Rounds on other seeds are the remaining hole: a thrown round there costs nothing. So
only daily rounds count toward an index (jdharms, 2026-11-17).

- **Every player starts from the starting index**, the site's current players as well
  as anyone who joins later: no round on another seed counts, before launch or after.
  Daily rounds from before handicaps launch (Milestone A) count, since they are daily
  rounds; their seeds are rated at launch.
- **The first dailies after launch favor the strong.** A regular who has played few
  dailies starts near scratch, so a weak regular receives far fewer strokes than they
  need until about 8 daily rounds have replaced the starting index, as any newcomer
  does (New players).
- Hole ratings still use every round on every seed.

### Seasons

**Problem.** A season needs a rule for missed days, and every such rule is exploitable.

A 28-day season scored by a player's best N days was simulated from the site's rounds:

- Summing raw Stableford points lets volume win (the most frequent player took 71% of
  seasons at best 10) or variance win (at best 3), and the strongest player won at most
  1% of seasons.
- Scoring each day by position in the field spreads the chances at best 3, but the best
  N rule makes a thrown day free: it is dropped from the season and still raises the
  handicap.

**Proposal.** No seasons. The calendar month is a view of records, a count that starts
again without a champion, a cutoff or a dropped day.

### Reasons not to play

**Problem.** A ranked table gives its leaders a reason to stop.

**Proposal.**

- **No handicap leaderboard.** It rewards protecting a number, its top is static, and it
  is where a reason to cheat would first appear. A player's index and low mark are shown
  on their own page.
- **Records are counts, not rates.** Wins, halves and losses, never a ranked win
  percentage.
- **Career counts only rise.** Dailies played, daily wins, match record, birdies and
  eagles, rounds under handicap.

Whether a monthly record discourages a player with a good month from playing on is to be
watched once it is live.

### Spoilers

**Problem.** Knowing the field's scores helps in a match, and a player who has not played
does not want to see them.

**Proposal.** A daily's scores are hidden entirely, not collapsed, from a viewer who has
not submitted a round on it. That covers the seed page, other players' round pages and
link preview cards, which never carry a score on any seed.

Once a daily closes, nobody can join its field, and its standing is public: positions,
names, Stableford points and skins, shown on the daily's page to anyone, and after
launch posted to Discord. Its scorecards are collapsed for a viewer who has not played it, as on any seed,
since a player may still play the seed as an ordinary round.

## Integrity

A cheated round cannot be prevented: an emulator has save states, and a player can
practice a seed before the round they submit. Until now nothing rewarded it.

**Proposal.** Flag by statistics and review by hand. The model gives every round an
expected score and a spread, so a round far better than expected goes to an admin, who
can already flag and void rounds. On the 2026-11-17 backtest a round's total less its
expected score averages +2.2 strokes, since an index reads optimistic (The handicap
index), with a spread of 6.5 strokes; measured from that average, no round of 236 was
more than 2 spreads better than expected. The spread is far wider for weaker players, so
the threshold is per index band and measured from the average (implementation task 28).

The flag is not what stops sandbagging or a strong player's new account: best 8 of 10,
the starting index and exceptional score reduction are. A run of rounds far worse than
expected is flagged for review too; a single bad round cannot be told from a bad day.

Turned down: linking Twitch accounts and requiring a stream of the daily. It is more
data about players, and a requirement to stream is a reason not to play.

Removing the handicap leaderboard removes most of the prestige a cheat could win. A
practiced or cheated round also lowers the player's index, which costs them in every
later net result.

## Later, on top of the daily

- **Short events.** Three or four dailies as one tournament, a few times a year.
  Handicaps are frozen at the start and every round counts, so being behind lasts a
  weekend and a thrown round cannot help. The four-pin course of
  `docs/tournament_days.md` fits.
- **Teams.** Each day's field is paired for net best ball, or a month is played between
  two sides balanced by handicap. A team absorbs a bad day, every round helps someone,
  and a thrown round hurts a partner.

## Open questions

| Question | Leaning |
|----------|---------|
| A daily's settings: course mode, transforms, wind, club rules | A request per weekday, as in The week: an arc from Monday to Saturday, Casual Friday's par 3s, and Sunday Classic as the one day that needs only the US ROM |
| Calendar months or a rolling 30 days for records | Calendar months, per jdharms; to be tested |
| The numbers | Set on the 2026-11-17 backtest (Setting the numbers); run again on a fresh database before launch, then left alone |
| Whether a player's page is public | Undecided |
| Net cards for ordinary rounds, shown and stored | Feasible from what is stored (jdharms, 2026-11-17: revisit later). Every seed's ratings are fixed when it is generated, and the index a player held when a round was scanned can be recomputed from their daily rounds before it, so past rounds could be filled in as a scan would have shown them. It would need a table keyed by round, the parameters in force at each scan stored with it, and no card for a player 2 round. Ordinary rounds would still not count toward an index unless ADR 0034 changes |

## Evidence

The numbers above come from `tools/spikes/handicap_backtest.py`, run on the site's
database of 2026-11-12: 225 rounds, 14 players. For each round, the handicap came from
that player's earlier rounds on other seeds. Every pair of players on every shared seed
was scored: 211 pairings over 53 seeds, among players with at least 3 earlier rounds.

| Handicap | Stronger player, match play | Stronger player, net stroke play |
|----------|-----------------------------|----------------------------------|
| None | 88% | 90% |
| Flat, all rounds | 44% | 47% |
| Daily, all rounds | 45% | 49% |
| Daily, last 8 | 48% | 51% |
| Daily, last 8, 90% | 55% | 57% |
| Daily, best 8 of last 20 | 53% | 55% |
| Fractional personal par | 38% | 49% |

Its limits:

- Nine players qualify, and three of them are in most pairings.
- Hole ratings were fitted with later rounds on other seeds, which a live system would
  not have.
- The season simulation draws each player's days from their own past, so it treats a
  fast improver's results as permanent.
- The caps and the daily-rounds-only rule are untested. The backtest's indexes come from
  rounds on every seed.
- Its ratings are measured against the field, not the reference holes, it has a
  provisional period rather than a starting index, and its stroke index ranks by spread.
