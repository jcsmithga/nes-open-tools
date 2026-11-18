+++
status = "proposed"
date = 2026-11-18
area = "site"
permanent = false
revisit_when = "a second kind of competition, an official event or a game players arrange, is designed, or a round needs to be in more than one competition"
drafted_by = "Claude"
supersedes = []
+++

# Competition tables stay specific to the daily until a second kind of competition is designed

## Context

The daily (ADR 0033) is the site's only competition, and its tables are its own:
`dailies`, `daily_seeds`, `daily_cards` and `daily_card_holes`, and the results in
`daily_results` and `daily_matches`. Others are expected:

- **Official events**, run by an admin: majors, one-off events, several dailies as one
  tournament (implementation task 32). They score net with players' indexes, and their
  rounds count toward them.
- **Games players arrange** between themselves: skins, match play. They score net with
  players' indexes, and their rounds never count toward them.

A daily card is one case of something more general: a player's place in a competition,
the round they played in it, and what the competition made of it under its own rules.
Building that general shape now was considered while making indexes snapshots taken
when each round is scanned (ADR 0034).

## Decision

**The tables stay the daily's.** Nothing is built for competitions in general until the
second kind is designed. The rules that any later competition follows are already
decided, in ADR 0034 or below, so they need no tables to hold:

- **Who creates a competition decides whether its rounds count.** Only one the site or
  an admin runs counts toward an index. If players could arrange competitions that
  count, two of them could play any number of games between themselves, throw every
  round, and take dailies on the strokes. A game players arrange reads their index and
  never changes it.
- **Counting is decided when a round is scanned, and never after.** A competition that
  counts exists before the rounds that count in it, so a round's index row is written
  once. A game made afterward from rounds already played is fine, since it does not
  count.
- **A round counts at most once**, however many competitions it is in.
- **The index rules are the site's**, recorded with each counted round, never a
  competition's: a major and a daily on the same seed cannot score one round's
  differential two ways. A competition's rules are its allowance, its formats and its
  handicap policy.
- **A competition chooses the index it scores with.** At scan, the round's stored
  `index_before`, is the usual policy: dailies use it and games will by default.
  Frozen at the start, the index a player held when they joined, is for events that
  want it (task 32). A competition's card therefore keeps the index it used beside its
  handicap, even where that equals the round's.
- **Withdrawing a competition** removes its results, and its rounds count toward no
  index unless another competition that counts holds them.

## Rejected alternatives

- **General competition tables now.** The likely shape:

  ```
  competitions        id, kind (daily | event | game), created_by, counts (site or admin
                      only), rules (allowance, formats, handicap policy), opens_at,
                      closes_at, withdrawn_at
  competition_seeds   competition, seed, position (a daily has one, a four-day major four)
  competition_players competition, user, joined_at, the index frozen at joining when the
                      policy says so
  competition_cards   competition, user, round public_id, index_used, handicap; keyed by
                      round, since a multi-round event gives a player several
  competition_card_holes, and results by format: standings, matches, skins
  dailies             the competition, and what only a daily has: its date, theme,
                      request and reruns
  ```

  Under it a round counts while it has a card in a competition that counts and is not
  withdrawn, the same join the index already makes through `daily_cards`. It was turned
  down because the second kind's needs are unknown: registration, several rounds a
  player, frozen handicaps, brackets, perhaps teams. Tables designed around one kind
  would likely be migrated again once the first event or game is specified, while
  waiting costs one join in the index's read when `daily_cards` is renamed. The data on
  the site is small enough to migrate whenever that is.

## Consequences

- **The index tables do not depend on the shape of competitions.** A round's index row
  (ADR 0034) holds the step, the index rules it was scored under and nothing about the
  competition that counted it, so generalizing changes only the join that finds a
  player's counted rounds.
- **The first event or game is a schema change**, from the shape above unless its
  design argues otherwise.
- **Until then, only dailies count**, and nothing on the site can arrange a competition
  of any other kind.

## Sources

- `docs/competitive/design.md`, `docs/competitive/data_model.md` and
  `docs/competitive/implementation_plan.md` (tasks 32, 47 to 49).
- Design session with jdharms, 2026-11-18 (session 052e40ef), on showing the index a
  player played a seed with.
