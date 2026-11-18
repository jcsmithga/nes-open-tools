+++
status = "accepted"
date = 2026-11-17
area = "site"
permanent = false
revisit_when = "a re-estimate moves gamma past about 0.6, the stronger player's steady-state share of net matches no longer holds 55% across resampled rosters, a month of dailies lets the daily-rounds-only rule be tested, or the field grows enough to rate each layout's spread"
drafted_by = "Claude"
supersedes = []
+++

# Handicaps rate every seed once from hole ratings anchored to par on the NES Open holes, and an index is the best 8 of the last 10 daily rounds, updated as each is scanned

## Context

The daily (ADR 0033) is scored over its field with handicaps, so players of every level
can compete: ratings on the site run from about 15 under to 55 over a reference course,
and without handicaps the stronger of two regulars takes 87% of their matches.

A generated seed has never been played, but its holes have: the site's hole scores rate
every hole, and every seed is a set of rated holes. The model needs an anchor, since a
stroke added to every player and taken from every hole predicts the same scores; the
spike anchored to the field's average, which moved every index whenever players joined.

The field is small (18 players and 285 rounds on 2026-11-17, three players in most
shared seeds), still improving, and varies widely: a weak player's rounds spread twice
as far as a strong one's. Every number has to be set from that data or borrowed from
golf, and the server that runs the site is weak.

## Decision

**The hole model.** Every hole score is one equation:
`strokes = rating(hole) + spread(hole) * index(player) / 18 + luck`, where a hole's
rating is the sum of par, distance and wind terms and nested layout, terrain and pin
effects, each shrunk toward its parent, and
`spread = 1 + gamma * (rating - 4)`. Skills are shrunk toward an unpenalized field term.
A hole nobody has played is rated from par, distance and wind. A hole mirrored and
otherwise unchanged is the same hole as its twin; a redrawn hole is keyed by the hole it
built.

**The reference.** Index 0 is a player expected to shoot par over the 54 untransformed
NES Open holes (US, UK and Japan), each hole's four pins averaged, with no wind, and the
fit is constrained so their ratings average par. Their mean spread is slope 1.0. A seed's
course rating is an index-0 player's expected score; its slope is its mean spread, which
with one gamma is `1 + gamma * (course rating - 72) / 18`. The Mario Open holes are left
out, as a check.

**Where it runs.** The model's parameters (the variance of each group, the luck on a
hole, and gamma) are estimated offline by EM run to convergence, with how certain gamma
is from resampled rosters, and checked in as `golf/ratings/data/parameters.json` with
the index rules and the allowance. The hole ratings are refitted under it once a day, at
the daily's rollover, in the app's own loop. Nothing runs on a timer.

**Seed ratings are stored once.** A seed is rated when it is generated, from the latest
refit, and its course rating, slope and stroke indexes never change; seeds made before
ratings existed are rated once at launch. Every seed is rated, so every round on a seed
is scored against them.

**Stroke index** ranks a seed's holes by rating less par, ties by spread.

**The index.** A round's differential is (adjusted gross - course rating) / slope, its
adjusted gross capping each hole at a net double bogey. The index is the mean of the
best 8 of the last 10 differentials, a shorter record keeping 80% of its rounds.

- A differential 7.0 strokes better than the index the player held takes 1.0 off every
  differential in the record, 10.0 better takes 2.0 (exceptional score reduction).
- Past 3 strokes over the low mark, the lowest index of the last 365 days once a player
  has 20 rounds, a rise counts half, and it never passes 5 over.
- Every player starts at an index of 0, worth 4 rounds, fading by half a round with each
  of their own until it is gone at 8. There is no provisional period: every player is in
  every result from their first daily.
- A round counts toward an index only when it is scanned into a competition the site or
  an admin runs, decided at scan and never after. A competition players arrange uses
  their index and never changes it (ADR 0035). Today the daily is the only competition
  that counts: daily rounds on days not withdrawn, Milestone A's included. A round on any
  other seed never counts, before launch or after, so the site's current players start
  from the starting index like anyone who joins.
- **A round keeps the index it was scored with.** When any round is scanned, daily or
  not, the index its player holds is stored with it. A counted round also stores its
  adjusted gross, its differential, any exceptional cut it earned, and the index and low
  mark after it, under the index rules then in force. None of it is computed again.
- **The index rules are the site's**, recorded with each counted round, not a
  competition's.
- **A player's index** comes from the stored differentials and cuts of their counted
  rounds that are still recorded, under the current index rules. Nothing upstream of a
  round's stored differential is computed again.
- **A void or a withdrawn day** removes the round from every competition it was in, and
  those are scored again. Its player's index is recomputed from what the remaining
  rounds stored. The rounds after it keep the index and handicap they were scored with.
- **Rounds from before handicaps launched**: daily rounds keep the values their cards
  were filled with at launch. Every other round has no index.

**The daily handicap** is `round(allowance * (index * slope + course rating - par))`,
rounded half up, its strokes given by stroke index, given back from stroke index 18 by a
player better than scratch. In a match the weaker player receives the difference.

**The allowance** is 90%, a round number chosen for a target: at the steady state, both
players with a full record of 10 rounds, the stronger player takes about 55% of their
net matches, a slight edge for skill. It is not solved for at each re-estimate; it
changes only if the target changes, or if the share's range across resampled rosters
no longer holds 55%.

**Setting the numbers.** Every other number starts from the World Handicap System's and
changes only when another value wins clearly, in at least 95% of 1,000 rosters resampled
from the players, on `golf-ratings backtest`, which replays the database day by day with
only what was known each day. The exceptions, chosen for what they do rather than for a
backtest score, are best 8 of 10, the starting index of 0 and its weight, and the stroke
index order.

## Rejected alternatives

- **Anchoring to the field's average.** Every index and course rating would move as
  players join and leave: the spike's offset to par moved by 3 strokes in three weeks.
- **Anchoring to a few named players**, or to the baseline terms alone: the first drifts
  as those players improve, the second rests on the model's least reliable part.
- **A skill per player per month**, or one that drifts. It moves live seeds' course
  ratings 0.19 strokes on average and 0.80 at most, under the stroke that would adopt it.
- **Decaying old rounds for hole ratings.** A hole does not change, so an old round says
  as much about it as a new one.
- **A site-wide term for mirrored holes.** Compared within rounds that have both kinds, a
  mirror-only hole plays as its twin (-0.04 +/- 0.08 strokes a hole); fitted as a term it
  looked significant only because it took in players' form.
- **Re-rating seeds as the model improves.** It changes nothing within a daily, whose
  field shares one rating, and moved indexes without play by up to 1.4 strokes in four
  weeks.
- **A nightly job for indexes.** With seed ratings stored, only a scan, a void, a
  restore or a withdrawal changes an index.
- **An index derived from the player's whole history on every read**, the first design.
  A void, a withdrawn day or a change to the index rules moved every later index,
  handicap and result, so a net card, a match or a skins game could change days after a
  player saw it.
- **The last 8 rounds.** Fair at the steady state, but a round thrown by 15 strokes buys
  13 to 19 strokes of index over the next 8 rounds, against 4 to 10, and a volatile
  player's blowups raise their index.
- **Best 8 of the last 20**, the World Handicap System's rule: it drops 12 of 20 rounds,
  which punishes erratic players.
- **Seeding current players' indexes from their rounds on any seed** at launch. They
  would start with accurate indexes, but from rounds nobody played as a competition,
  which could have been thrown for nothing (jdharms).
- **A provisional period.** Its rounds would feed the index and count in no result, so
  they would be free to throw.
- **A generous starting index**, such as +4.5. It predicts the typical newcomer better,
  but a strong player new to the site, or a strong player's second account, would take
  dailies on the strokes it gave them.
- **An allowance of 100%**, a handicap's textbook goal. At 50% skill wins no more matches
  than luck, and in a field the more variable weaker players win more than their average
  deserves.
- **Ranking stroke indexes by spread.** The fairest by the model, but par 5s would nearly
  always take indexes 1 to 4; the backtest finds no difference in fairness.
- **A Twitch stream or other proof of a round.** More data about players, and a reason
  not to play; a round far better than expected is flagged for review instead.

## Consequences

- **A seed's handicap numbers exist before anyone plays it**, so any seed's page can show
  a player their handicap, and the daily needs nothing computed at open.
- **What a player was shown stays as shown.** A net card, a match or a standing changes
  only when its own field changes. A fix to the index code changes future rounds.
  Rewriting past ones is a deliberate migration.
- **Every round has an index**, so a seed page shows a player who has played it the
  index and handicap they played with, and a later competition on any seed can score
  rounds with it.
- **Rescaling the index**, as anchoring on both games' holes would, has to convert the
  stored indexes and differentials along with the seed ratings, or keep them as shown
  with their scale recorded.
- **Indexes read optimistic**, as golf's do: a round averages about 2 strokes worse than
  its index predicts. The integrity flag measures from that average.
- **A new player is smacked around** for their first dailies, while their index rises from
  scratch; a strong newcomer is caught within a round or two. At launch that includes
  every current regular with few daily rounds: the first weeks of handicapped dailies
  favor the strong.
- **A seed rated early keeps an early rating.** As the model matures this costs less; new
  kinds of hole, such as unplayed derived par 3s, are rated from their baseline until
  played.
- **Gamma is the soft number.** On 2026-11-17 it is 0.443, with 90% of resampled rosters
  between 0.35 and 0.95, and slope follows from it alone. A spread for each layout is
  tested at a later re-estimate (implementation task 34).
- **The numbers are re-checked**, not re-solved: each re-estimate reports the
  within-round mirrored test, the per-month comparison and gamma's range, and each
  backtest the stronger player's share against the target.
- **The daily-rounds-only rule is untested** until dailies accumulate.

## Sources

- `docs/competitive/design.md` (Handicaps, Incentives, Integrity) and
  `docs/competitive/data_model.md`; the decisions made building it are D32 to D66 in
  `docs/competitive/implementation_plan.md`.
- The code: `golf/ratings/` and `golf-ratings`; the spikes `tools/spikes/player_skill.py`
  and `tools/spikes/handicap_backtest.py`.
- Design session with jdharms, 2026-11-17, on the production database copy of that
  morning (285 rounds, 18 players).
- Amended 2026-11-18, design session with jdharms (session 052e40ef): indexes are
  stored when each round is scanned rather than derived, which rounds count, and the
  index rules belong to the site.
