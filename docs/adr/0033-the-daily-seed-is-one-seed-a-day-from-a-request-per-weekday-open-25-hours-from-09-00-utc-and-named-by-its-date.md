+++
status = "accepted"
date = 2026-11-16
area = "site"
permanent = false
revisit_when = "the field outgrows a single daily, players outside North America make 09:00 UTC a playing hour, or the weekday themes stop drawing a shared field"
drafted_by = "Claude"
supersedes = []
+++

# The daily seed is one seed a day from a request per weekday, open 25 hours from 09:00 UTC and named by its date

## Context

The most common request from players is a seed of the day: a way to play against others
without arranging it. Competitive play (`docs/competitive/design.md`) makes the daily its
unit of competition: everyone who submits a round on a day's seed while it is open is in
that day's field, and handicapped results are scored over the field.

Fields will be small. About 10 players were active in the two weeks before 2026-11-12, the
site records about 4 rounds a day, and a daily is likely to draw 3 to 6, so every
unusual mode splits the field further. The site's rounds from 2026-09-20 to 2026-11-16
all come from North America: none arrived between 06:00 and 11:00 UTC but one at 06:00,
and a round arrives a median 42 minutes after a player's first download of a seed.

The settings are in a public repository, and anyone can run the generator.

## Decision

**One seed a day.** The site generates a daily from a request (ADR 0016) and stores it as
an ordinary seed. It is the competition; records, events and teams build on it without
changing it.

**The window.** A daily opens at 09:00 UTC and closes 25 hours later, at 10:00 UTC the
next day. The extra hour is a grace period for a player who downloads just before the
next daily opens. For that hour two dailies are open: the site shows the latest as the
Daily Seed, and the older one is reached from an open tab, a link or the calendar of
past dailies. A round arriving after a daily closes is an ordinary round on its seed.

**Its date.** A daily's date is its own, the day it opens at 09:00 UTC, not the calendar
date anywhere when it opened. Its magic words are that date: the weekday (`MON`, `TUES`,
`WED`, `THURS`, `FRI`, `SAT`, `SUN`), the three-letter month and the ordinal day, in US
order, such as `MON NOV 16TH`. A manifest's magic words may be 3 to 6 characters; the
word bank's stay 4 to 6.

**Reruns and withdrawals.** A daily's seed is replaced only when nobody can complete it,
and at most once. A round recorded on it shows it can be completed, so replacing is
refused once it has one. The replacement's magic words drop the weekday and end in
`RERUN`, such as `NOV 16TH RERUN`, so a player holding the broken ROM sees a mismatch.
When a replacement breaks too, or some players complete a seed others cannot, the day is
withdrawn: it has no results and no records, and its rounds count toward no index.

**Bags.** Players choose their own bag on a daily, as on any seed, unless the day's
request sets club rules.

**The schedule.** Each weekday has its own request, checked into the repository, with a
table of dated overrides for events, holidays or a day of newly published holes.

- Difficulty rises from Monday to Saturday, after the New York Times crossword, as a
  prescription rather than a fit to when players play. Friday and Sunday step outside
  the arc.
- Standard and natural par fill most days, as the modes players share.
- Sunday needs only the US ROM.
- The site publishes each weekday's theme, not its settings. A day's settings show on
  its seed page once it opens.
- A seed is never regenerated until its rating fits its day.

The current requests and the reasons for each are in `docs/competitive/schedule.md`.
They are tuned there, and a change to them is not a change to this record.

**The PRNG seed** is a keyed hash of the date under a server secret, cut to the
manifest's 16 hex characters. The date makes generation idempotent, so a missed run
regenerates the same seed; the secret stops anyone with the public requests and the CLI
from generating a daily ahead of time and practicing it.

## Rejected alternatives

- **A 24-hour window.** A player who downloads late in the day has no time to finish.
- **A grace period for players who downloaded before the close**, with no overlap. It
  helps only the players the grace is for, but adds a rule tying a round to its
  download for an hour a day that, at 09:00 UTC, nobody has needed.
- **Rolling over at a local hour**, such as 4 AM Eastern. A fixed UTC hour moves an hour
  locally with daylight saving, and both positions are inside the quiet hours.
- **Random magic words.** They show that a ROM is a seed's, but not that it is today's;
  yesterday's ROM is the likely mistake.
- **The year in the magic words.** Nobody confuses today's ROM with the same date a year
  ago. The weekday ties the seed to its theme.
- **Replacing a seed some players completed.** It is unfair to the players who already
  submitted.
- **Locking bags on dailies.** Nothing about competition needs it; a day that wants a
  fixed bag sets club rules.
- **One request every day.** Players differ in the modes they play; a weekly arc gives
  them variety without splitting a day's field.
- **The schedule's settings in this record.** They will be tuned far more often than the
  rules above.
- **Gating seeds on their rating.** Generating until a course rating falls in a day's
  band would change the odds the request states. Each daily's ratings are stored, and
  show over time whether the days differ as intended.
- **A PRNG seed from the date alone.** The requests are public, so anyone could generate
  next week's dailies.

## Consequences

- **Daily rounds belong to a date, not to a calendar day.** Results, records and months
  follow the daily's date, and the overlap hour needs no special case beyond which daily
  the site shows as the Daily Seed.
- **The magic words' minimum length drops to 3** for manifests, which no ROM has used.
  The first daily build should be checked on screen.
- **The server secret is a credential.** A leak lets anyone generate dailies ahead of
  time; changing it changes only the days not yet generated.
- **A missed generation run catches up** with the same seed, so the job can be retried
  freely.
- **A broken daily has one recovery**, the rerun; anything else is a withdrawn day.

## Sources

- `docs/competitive/design.md` (The daily), `docs/competitive/data_model.md` and
  `docs/competitive/schedule.md`.
- Design sessions with jdharms, 2026-11-12 and 2026-11-16; the window from the
  production database copy of 2026-11-16.
