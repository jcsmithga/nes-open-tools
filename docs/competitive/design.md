# Competitive play: design

> **Note**: This document was written by Claude, from a design session with jdharms on
> 2026-11-12. It records the problems competitive play has to solve and the current
> proposal for each. Nothing in it is built. The ordered work is in
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
| Hole rating | For one hole as built (layout, terrain, pin, wind): the strokes a reference player is expected to take, and its spread factor, how much it separates players of different skill |
| Course rating | The sum of a seed's 18 hole ratings: what the reference player is expected to shoot |
| Slope | The mean of a seed's 18 spread factors. 1.0 is an average seed |
| Stroke index | A seed's holes ranked 1 to 18 by spread factor, the order handicap strokes are given in |
| Handicap index | A player's skill in strokes per 18 holes on an average seed, from their recent rounds |
| Low mark | The lowest handicap index a player has held in a trailing window |
| Daily handicap | The whole number of strokes a player receives on one seed: index, slope, course rating and the allowance combined |
| Allowance | The share of a handicap a player receives. Below 100% it favors the stronger player |
| Net score | Strokes on a hole less the handicap strokes received there |
| Provisional | A player with too few rounds for a trustworthy index |
| Format | One way of scoring a daily's field. A daily is scored under several at once |
| Result | What a format gives a player for one daily: a standing, a match against each other player, skins won |
| Record | A count of results over time: career, by month, and head to head |

## Architecture

Competitive play is part of the randomizer site. The site already holds identity, entries,
rounds and the QR loop, so results need no scraping and no second system. A player who
finds the site finds the competition, with no chain of Discord servers to follow.

A Discord bot or a separate site on top of an API is how other randomizers do it, mostly
because their results cannot reach the randomizer. Ours can. Discord's part here is an
announcement when a daily closes, carrying its standing. A daily is always open, so its
opening is not news; its result is.

Ratings are computed by a scheduled job, not in a request. The fit takes minutes, and a
handicap that changes once a day is what golfers expect.

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
- Its course rating, slope and stroke indexes are fixed when it is generated, and each
  player's index is the one posted before it opened.
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
- **`/me` and round pages are unchanged.** A daily round is an ordinary round there,
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

Magic words today must be 4 to 6 characters, and `MON` and `NOV` are 3. The menu pads
every word to a 6-character slot and the scorecard title has 26 columns, so the fonts and
layout allow 3. The minimum drops to 3 for a manifest's words while the word bank keeps
4 to 6. A ROM with a 3-character word has not been built yet.

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

### Rating holes and courses

**Problem.** A generated course has never been played, and seeds range from par 54 short
courses to hard full ones.

**Proposal.** The hole model of `tools/spikes/player_skill.md` already rates holes: par,
distance and wind give a baseline, and every round played on a layout, a terrain variant
or a pin, in any seed, refines it. A generated course is rated the moment it exists,
because nearly all of its holes have been played elsewhere. A hole nobody has played
falls back on its baseline.

The model gains one term. The gap between two players is wider on harder holes: on the
2026-11-12 data, a hole one expected stroke harder separates players by about 46% more.
Each hole therefore has a spread factor, and the model is

```
strokes = hole + spread(hole) * skill(player) + luck
spread(hole) = 1 + gamma * (hole - mean hole)
```

Summed over 18 holes this is golf's own pair of ratings: the course rating is the sum of
the hole terms, and slope is the mean spread factor. From the current fit a par 54 short
course has a slope near 0.65 and a hard full course near 1.25.

The spread term is thinly supported: 14 players, and mostly one strong player against the
rest. An error in a course rating moves every player's handicap equally, so it cancels in
every comparison between players and shows only in scores against one's own handicap. An
error in slope does not cancel.

### The handicap index

**Problem.** Players improve. Against an index built from all of a player's rounds,
improving players beat their number by 2 to 7 strokes a round while the strongest player,
who has plateaued, beats his by 0.1. He then loses about 70% of his net matches.

**Proposal.** The index comes from a player's last 8 rounds. On the backtest this removes
the link between skill and net result (correlation +0.01 in net stroke play).

Turned down:

- **All rounds.** Lags improvement, as above.
- **Best 8 of the last 20**, the World Handicap System's rule. It balances the overall
  numbers but punishes erratic players, whose best rounds sit far from their usual ones:
  one +9 player fell to 0.26 match points.

### The daily handicap

**Problem.** A handicap has to be legible. A fractional adjustment on every hole is not.

**Proposal.** Golf's formula, giving a whole number of strokes for the day:

```
daily handicap = round(allowance * (index * slope + course rating - par))
```

Strokes are given by stroke index: one on each of the hardest holes until they run out,
a second lap past 18. A player better than scratch gives strokes back, starting from the
easiest hole. In a match, the weaker player receives the difference between the two
daily handicaps.

A fractional "personal par" for each hole was tested and was no fairer.

### The allowance

**Problem.** A full handicap favors the weaker player. Weaker players are still improving,
and their scores vary far more: net scores spread about 3 strokes for the strongest
player and about 8 for players near +9.

**Proposal.** A 90% allowance, treated as a dial.

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
to 3.5 after two or three.

**Proposal.** A player is provisional until they have 3 rounds that count toward their
index, which for a player who joins after launch means 3 dailies (see Sandbagging). A
provisional player plays the daily and sees their own results, but is left out of the
day's standing and skins and of other players' records.

## Formats

**Problem.** A format has to survive a blowup hole (6.3% of holes are 3 over par or
worse) and a field of two.

**Proposal.** One round is scored under several formats at once. A format is a way of
reading the same field's net scores, so adding one costs a player nothing and asks
nothing of them. Each daily produces three results for each player.

- **Standing**, by net Stableford points: 2 for a net par, 1 more for each stroke under,
  1 fewer for each stroke over, never below 0. A blowup costs one hole's points, and
  Stableford narrows the spread of erratic players from about 8 strokes to about 6. The
  standing decides the day's winner.
- **Matches**, net match play against every other player in the field, hole by hole.
- **Skins**, net, across the whole field: a hole is won by the one player with the lowest
  net score on it, and by nobody when that score is shared. Skins are for fun. They are
  a game of variance, not a fair contest: on the backtest the strongest player wins 0.3
  of a fair share with handicaps, and everyone else wins 0.15 to 0.3 of theirs without.
  Skins won are a career count and decide nothing else.

Turned down: gross play of any kind as the main competition, by constraint 1.

## Incentives

### Sandbagging

**Problem.** A handicap built from recent rounds can be raised by playing badly. With the
last 8 rounds, one round thrown by 15 strokes moves an index about 2 strokes.

**Proposal.** Three rules, all golf's own:

- **Every result counts.** No format drops a player's worst days, so a thrown daily costs
  that day's standing and every match in it.
- **A cap on each hole.** For the index, a hole counts no worse than a net double bogey.
- **A cap above the low mark.** Past 3 strokes over a player's low mark, a rise in index
  counts half; it never exceeds 5 over. The numbers are the World Handicap System's and
  need tuning for a field that is still improving.

Our estimate is that a thrown daily about breaks even without the caps and loses with
them. That estimate is not backtested.

Rounds on other seeds are the remaining hole: a thrown round there costs nothing. So
only daily rounds count toward an index.

- At launch, each current player's index is seeded from all of their rounds on any seed.
  From then on only their daily rounds are added.
- A player who joins after launch has an index from daily rounds alone.
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
names, Stableford points and skins, posted to Discord and shown on the daily's page to
anyone. Its scorecards are collapsed for a viewer who has not played it, as on any seed,
since a player may still play the seed as an ordinary round.

## Integrity

A cheated round cannot be prevented: an emulator has save states, and a player can
practice a seed before the round they submit. Until now nothing rewarded it.

**Proposal.** Flag by statistics and review by hand. The model gives every round an
expected score and a spread, so a round far better than expected goes to an admin, who
can already flag and void rounds. The threshold is to be set by backtest; 3 standard
errors is probably too loose.

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
| The numbers: 8 rounds, 90%, 3 rounds provisional, the caps, the flag threshold | Set by backtest before launch, then left alone |
| Whether a player's page is public | Undecided |

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
