# Generation Rewrite: a Spike

> **Note**: This document was written by Claude.

This is the report of a spike on the `generation-spike` branch. It replaces the hole
draw's two fixed stages (draw a layout of pars, then fill it) with one question: which
eighteen holes of the pool keep a list of rules? The spike was built to find out whether
that model holds up, what it costs and where it breaks. Nothing here is a decision.

Code: `golf/randomizer/rules.py` (the vocabulary), `golf/randomizer/compose.py` (a pool
and rules in, a course out), `golf/randomizer/solver.py` (the search),
`golf/randomizer/request.py` (settings files), `golf/randomizer/survey.py`
(measurement), and the requests in `data/presets/`. Background:
[manifest.md](manifest.md), [thoughts_on_par_6.md](thoughts_on_par_6.md),
[derived_holes.md](derived_holes.md), ADR 0016 and ADR 0023.

## What was found

- **The model holds.** 35 requests are checked in, from the eight vanilla courses and a
  par 3 course to a course whose 18th is its hardest hole. Every one generates, in 60 ms
  or less on average, with no failure in 500 courses each.
- **A standard course comes out as it did before.** Over 4,000 courses on each of three
  settings, the new draw and the old agree on predicted difficulty, yardage, how many
  expert holes a course has and how often each family appears. The one measurable
  difference is at the ends of the course, and it is two points.
- **The build did not move.** The golden unfinished-IPS hashes still match when their
  manifests are given the holes the old draw drew. Six presets, par 54 and par 58 among
  them, build into ROMs whose tables hold the course. None of those ROMs has been played.
- **The draw is not uniform over the courses that keep the rules.** It runs toward
  limits: under a cap, more courses sit at the cap than a uniform draw would put there.
  That is what the old capped draw did too, and for a per-nine ceiling on difficulty it
  is the behavior wanted. It is measured below, and it is the main thing to understand
  before adopting this.
- **The lean can be taken out afterward, for loose rules cheaply.** A pass of random
  changes that keep the rules (`Problem.mix`, off by default) brings a standard course
  with the expert cap, or a per-nine ceiling, to the uniform draw's figures in 200 to
  800 steps, about 3 to 10 ms. Rules with an order to keep, or a course near the
  longest the pool allows, are still moving after 1,600 steps.
- **A naive search is not enough.** The first version thrashed on a standard course.
  Seven additions to the solver were needed, each for a kind of rule the presets use.
  They are most of the solver's thousand lines, and the real cost of the design.
- **Impossible requests mostly fail fast and say why.** Of 600 random rule sets, 473
  found a course, 116 were proved impossible (usually in about 20 ms, naming the rules
  that clash), and 11 gave up after a few seconds without proof.
- **A per-nine ceiling on predicted strokes works.** Each nine held to 3 over par, with
  no cap on expert holes, gives a mean of +5.1 with a spread of 0.8. The expert cap of 1
  gives +5.7 and 1.6.
- **What it cannot say** is anything about transforms, wind or music, anything that is a
  preference and not a limit, and a probability such as "a par 6 in one seed of four".
  ADR 0016's weighted requests cover the last.

## The model

A course is eighteen slots. Each takes one hole of the pool. A rule is a JSON object
with a `rule` name and arguments, and a course must keep every rule in the list. Rules
only add: there is no "or" between rules and no way to take one back.

Seven general rules do the work, and the rest are written in them:

| Rule | Holds when |
|---|---|
| `count` | the number of holes in a scope that match a selector is within `min`/`max` |
| `sum` | the total of a number over a scope is within `min`/`max`; with `where`, over the holes that match |
| `compare` | one scope's count or sum, less another's, is within `min`/`max` |
| `limit` | no more than `max` holes of a scope share a family, course, ROM, par, ...; with `min`, each has at least that many |
| `step` | a number changes by `min` to `max` from each hole of a scope to the next |
| `gap` | a number differs by at least `min` between neighbors |
| `weight` | (never broken) holes that match are `factor` times as likely to be picked |

- **A selector** (`where`) describes holes by `par`, `tag`, `rom`, `course`, `kind`,
  `family`, `id`, `author`, `distance`, `over_par` and `original_hole`, with `not` and
  `any` to combine them. A curation tag is a selector the day it is added, with no code.
- **A scope** says which slots a rule looks at: `course`, `front`, `back`, `each_nine`,
  `each_hole`, a list of hole numbers, a range, or `{"window": 3}` for every run of
  three holes.
- **The numbers** are `par`, `distance`, `over_par` (from `data/difficulty/holes.json`)
  and `original_hole`.

The named rules are shorthand. `golf-randomize rules` lists them all.

| Rule | Stands for |
|---|---|
| `standard` | `par_counts` for the par, `balanced_nines`, `no_consecutive` par 3s and 5s |
| `par_counts` | a `count` for each par, exact or a range |
| `balanced_nines` | a `compare` of front and back for each par |
| `no_consecutive` | a `count` over every window |
| `total_par` | a `sum` of par |
| `expert_cap` | a `count` of expert holes on each nine |
| `distinct_families` | a `limit` per family |
| `slot`, `pin`, `exclude`, `require` | a `count` over one slot, or over a scope |
| `vanilla_course` | a `slot` for each hole: this course's hole of that number |
| `highest`, `lowest` | a `step` from every other hole to one slot |

### Settings

`settings.constraints` holds the list, in manifest schema 5. `null` is the default
course, `[{"rule": "standard", "par": <par>}, {"rule": "expert_cap", "per_nine": 1}]`.
A list is the whole of the rules: nothing is added to it but the family rule, so a
course with a cap on expert holes says `expert_cap` and one without leaves it out.

Schema 5 has no `draw_rule`. Schemas 3 and 4 held the expert cap there. Their seeds are
read only as stored manifests (ADR 0024), which keep a `draw_rule` as issued and show a
schema 5 seed's `constraints` as the draw rule they amount to (`docs/manifest.md`,
**Reading stored manifests**).

This leaves `par` saying nothing when `constraints` is a list. See **Open questions**.

### Requests and presets

`golf/randomizer/request.py` reads a settings file: a partial settings object over the
defaults. It is the unweighted half of ADR 0016's request and refuses `one_of` and
`sets` by name. `data/presets/` holds 35 of them, and `golf-randomize generate --preset
NAME` rolls one. Two examples:

```json
{
  "about": "Mario Open's UK course, holes 1 to 18 as the cartridge has them.",
  "sources": ["mario_open_jp"],
  "constraints": [{"rule": "vanilla_course", "course": "jp_uk"}]
}
```

```json
{
  "about": "A standard par 72 with no cap on expert holes, but each nine's predicted strokes over par held to 3: a hard hole is paid for by easy ones around it.",
  "constraints": [
    {"rule": "standard", "par": 72},
    {"rule": "sum", "of": "over_par", "scope": "each_nine", "max": 3.0}
  ]
}
```

A vanilla preset has no `expert_cap`, because the rules do not bend for a pin: Mario
Open's Hawaii course has more than one expert hole on a nine, and with a cap of 1 it is
refused, with a message naming the two rules. The 23 presets that cap expert holes at
one a nine say so in their lists.

`about` is a key this spike added for a line of text, since JSON has no comments. The
text in the presets is a description for the repository, not site copy.

## The draw

The search fills the slots in a shuffled order, in two passes.

1. **Kinds.** Each slot is narrowed to one kind of hole. A kind is whatever the rules
   can tell apart: for a standard course with an expert cap, a par and whether the hole
   is an expert one.
2. **Holes.** Each slot takes a family, each as likely as any other still open to it,
   and then one of that family's holes still open to it.

After every choice the rules remove what can no longer be used from the other slots. A
slot left with nothing undoes the last choice.

The first pass is the old layout stage, back again. The difference is that it is found
by the search, with the pool and every rule in view, and not drawn blind from a table.

Two properties hold for any rules:

- **Holes no rule tells apart are equally likely.** The search treats them alike at
  every step, so nothing but a rule or a weight can favor one.
- **A hole with a twin is no likelier than one without.** A family is picked before a
  hole, as before.

Everything else about how often a hole appears is measured, not derived.
`golf-randomize survey` does the measuring.

The search is bounded in steps, not time, so the same inputs stop at the same place on
any machine. It tries a shuffled order six times with a short limit, then the smallest
domain first. A search that will find a course usually finds it at once or not for a
long while, so many short tries beat a few long ones.

### What the solver needed

A plain search with one propagator a rule failed on a standard course: it gave up, or
took 35 ms and 226 dead ends when it did not. Each addition below was made for a
specific failure.

| Addition | What failed without it | After |
|---|---|---|
| Kinds before holes | 80 par 4s refused one at a time for a slot no par 4 could fill | One refusal a kind |
| A sum and a difference read together | Four par 3s and balanced nines: three could land on the front before anything objected | A standard course takes 0.1 dead ends |
| Sums that know the holes differ | A course under 6,950 yards: 4 of 40 gave up, 1.3 s each | 15 ms, none fail |
| Counts checked together (`Tally`) | Twelve expert holes on a standard course: 343 dead ends, 200 ms | 2 dead ends, 19 ms |
| Sums in that check | 8,400 yards gave up after 13 s | Found in 25 to 50 ms; 8,500 proved impossible in 60 ms |
| Many short tries | Each nine a stroke under par: 26 of 30 found, 767 ms each | 30 of 30, 129 ms |
| A limit on work, not dead ends | Three random rule sets took 90 to 130 s to give up | Bounded at a few seconds |

Two smaller ones came from the fuzz run: rules that bound the same sum are read
together, and a cap on every run of holes becomes a cap on the course. A standard course
with no two neighbors of the same par, which needs ten par 4s that never touch, is now
refused at once. Before, the search gave up on it after three seconds.

The solver is checked against brute force: 6,000 random small problems agree on whether
an answer exists, and on 600 more every valid answer was drawn at least once. The same
tests run in the suite at a smaller count.

## What it can express

All 35 presets, 500 courses each (20 for the fixed ones). "Share" is the least and most
often any hole that appeared was on a course.

| Preset | Rules beyond `standard` | ms | Par | Yards | Over par (mean, sd) | Experts | Share % |
|---|---|---:|---|---:|---|---:|---|
| `vanilla_<course>` (8) | `vanilla_course`, no `standard` | 0.4 | 72 | 7,037-8,412 | +1.2 to +22.4 | 0-14 | 100 |
| `vanilla_front_nine` | `vanilla_course` on the front | 5 | 72 | 7,074 | +2.8, 1.1 | 0.9 | 3-100 |
| `par_3_course` | `par_counts` 18 par 3s, no `standard` | 8 | 54 | 3,728 | +5.9, 1.8 | 1.0 | 35-81 |
| `executive` | 14 par 3s, 4 par 4s never adjacent | 7 | 58 | 4,581 | +5.8, 1.7 | 1.2 | 2-65 |
| `executive_mixed` | no par 5s, 8-12 par 3s, par 60-64 | 10 | 60-64 | 5,823 | +5.8, 1.5 | 1.4 | 6-39 |
| `short_front` | par 3 front nine, 2/5/2 back | 4 | 63 | 5,567 | +5.9, 1.9 | 1.4 | 3-51 |
| `natural_par` | par 68-76, no layout rule but adjacency | 5 | 68-76 | 7,393 | +5.7, 1.6 | 1.7 | 4-23 |
| `shape_by_distance` | 4 short, 4 long by yardage; one 750+ at most | 5 | 72 | 7,406 | +5.6, 1.5 | 1.7 | 5-23 |
| `finishing_fives` | 9 and 18 par 5s, 1 not | 10 | 72 | 7,423 | +5.7, 1.5 | 1.7 | 5-22 |
| `palindrome` | pars read the same from either end | 11 | 72 | 7,399 | +5.6, 1.5 | 1.7 | 5-23 |
| `compensating_nines` | each nine's over par at most 3.0, no cap | 15 | 72 | 7,328 | +5.1, 0.8 | 2.3 | 4-42 |
| `even_nines` | nines within 50 yards and 0.25 strokes | 26 | 72 | 7,406 | +5.6, 1.6 | 1.7 | 3-29 |
| `harder_coming_home` | back nine 2 strokes harder | 14 | 72 | 7,372 | +5.9, 1.7 | 1.7 | 4-42 |
| `grand_finale` | 18 a par 5 and the hardest; 1 the easiest | 13 | 72 | 7,472 | +5.6, 2.5 | 1.3 | 2-24 |
| `expert_gauntlet` | at least 12 expert holes | 19 | 72 | 8,041 | +18.5, 1.5 | 12.0 | 1-94 |
| `big_ones` | 6 holes expert or 600+ yards | 12 | 72 | 7,651 | +10.5, 2.1 | 5.2 | 4-48 |
| `tips` | at least 7,900 yards | 14 | 72 | 7,902 | +8.4, 1.0 | 1.7 | 2-91 |
| `forward_tees` | at most 6,950 yards | 14 | 72 | 6,946 | +3.0, 1.1 | 1.7 | 4-89 |
| `long_short_holes` | the par 3s total 900 yards | 14 | 72 | 7,486 | +6.3, 1.5 | 1.7 | 3-74 |
| `ladder` | 4/10/4, each hole longer than the last | 9 | 72 | 7,376 | +5.3, 1.9 | 1.5 | 1-36 |
| `change_of_pace` | neighbors 60 yards apart | 11 | 72 | 7,290 | +4.9, 1.5 | 1.7 | 5-53 |
| `world_tour` | 2-3 holes a vanilla course, never adjacent | 60 | 72 | 7,412 | +5.7, 1.4 | 1.8 | 5-25 |
| `in_position` | every hole at its own number | 8 | 72 | 7,363 | +5.0, 1.5 | 1.5 | 4-28 |
| `running_order` | original hole numbers never go down | 15 | 72 | 7,400 | +5.4, 1.5 | 1.7 | 2-30 |
| `nes_out_mario_in` | front nine NES Open, back Mario Open | 10 | 72 | 7,289 | +4.1, 1.3 | 0.9 | 2-21 |
| `alternating_roms` | ROMs alternate | 13 | 72 | 7,342 | +4.8, 1.5 | 1.6 | 3-22 |
| `signature_holes` | two named holes, anywhere | 11 | 72 | 7,683 | +8.7, 2.3 | 3.7 | 3-100 |
| `mario_heavy` | Mario Open holes weighted 4x | 11 | 72 | 7,540 | +6.6, 1.5 | 1.9 | 2-31 |

`tests/unit/test_compose.py` holds a check for every preset, written out in plain
Python with none of the solver's code, and draws each six times against it.

### Ideas that needed a new building block

Each of these could not be written at first.

- **Every hole at its own number.** A selector described a hole and nothing else. It now
  can depend on the slot: `{"original_hole": "slot"}`. `vanilla_course` is built on it.
- **The hardest hole last.** `step` related neighbors only. A scope can be any two
  holes, so `step` over `[5, 18]` relates the 5th and the 18th, and `highest` is
  seventeen of those.
- **If the 9th is a par 5, the 10th is a par 3.** `compare` took one measure for both
  sides. Each side can now have its own, and one count no greater than another is an
  implication. "Both or neither" is the two counts equal.
- **Expert or 600 yards.** A selector's fields all had to match. `any` takes a list.
- **The par 3s total 900 yards.** `sum` added over a whole scope. `where` narrows it.
- **Holes in their original order.** `original_hole` was a selector field and is now
  also a number, so `step` can read it.
- **Two or three holes from each vanilla course.** `limit` had a `max` only.

Three more were gaps in the solver, not the vocabulary: a course near the longest or
shortest the pool allows, a ceiling on a nine's difficulty, and a floor on expert holes.
All three could be written from the start and gave up when run.

### What it cannot say

- **Anything but holes.** A slot takes a hole. A mirrored hole, a wind anchor and the
  music are not values, so "a mirrored vanilla course" or "the 18th into the wind" is
  out of reach. Transforms and wind are decided after the holes, as before.
- **A preference.** There is no "as even as possible". A rule is a threshold, and
  `survey` is how to find one worth setting.
- **A probability.** "A par 6 in one seed of four" is not a rule about one course. It is
  ADR 0016's `one_of` over two lists of constraints. `weight` moves the odds but the
  result has to be measured.
- **"Or" between rules.** Only what a comparison of counts can stand for.
- **What the catalog does not record.** No rule can ask for water or a dogleg.
- **A par 6.** The rules place one without change (below). The manifest's slots still
  refuse a par outside 3 to 5, and the ROM is unverified.
- **Anything across seeds**, such as a season in which no hole repeats. An outside tool
  can write `exclude` rules from last week's manifest.

Not tried: a rule that one named hole comes before another.

## How the draw compares

### With the layout-first draw

4,000 courses each. `golf/randomizer/legacy_draw.py` keeps the old draw for this.

| Setting | Draw | Over par (mean, sd) | Yards (mean, sd) | Courses with 0 / 1 / 2 experts |
|---|---|---|---|---|
| Both ROMs, cap 1 | old | +5.69, 1.60 | 7,412, 178 | 1.6% / 23.8% / 74.7% |
| | new | +5.65, 1.59 | 7,412, 177 | 2.2% / 24.7% / 73.1% |
| Both ROMs, no cap | old | +7.82, 2.55 | 7,547, 217 | |
| | new | +7.76, 2.55 | 7,543, 218 | |
| US only | old | +1.49, 0.98 | 7,061, 121 | |
| | new | +1.54, 0.98 | 7,063, 121 | |

How often a family appears agrees to the decimal by par in all three (17.4%, 16.7% and
16.0% with both ROMs; 33.3% each with one). With no cap, the share of courses with each
number of expert holes, from 0 to 9, agrees within half a point.

The one difference is the layout. The old draw was uniform over 188,802 layouts, which
puts a par 3 on the 1st in 24.5% of courses and on the 9th in 20.6%. The new draw gives
22.7% and 21.4%: flatter, by two points.

### With a uniform draw

`survey --uniform` deals random courses and keeps the ones that pass, so every valid
course is equally likely. It is the yardstick, not a generator: one deal in 264 is a
standard course, one in 1,298 with the expert cap, and for 22 of the 27 presets that are
not fixed, 7 deals in 60,000 at best. 1,500 courses each:

| Rules | Draw | Over par (mean, sd) | Experts a course |
|---|---|---|---|
| Standard, no cap | composed | +7.80, 2.57 | same spread, 0 to 9 |
| | uniform | +7.85, 2.58 | |
| Standard, cap 1 | composed | +5.64, 1.56 | 2 on 72% of courses |
| | uniform | +5.36, 1.60 | 2 on 51% |
| Each nine at most +3.0 | composed | +5.07, 0.83 | 3 or more on 45% |
| | uniform | +4.13, 0.98 | 3 or more on 14% |

With no limit in play the two agree. With one, the composed draw sits nearer it. A slot
is filled without knowing how many ways the rest can be finished, so an expert hole is
as likely as its family is common until the cap closes, where a uniform draw would
already be weighing the courses each choice leaves.

For the per-nine ceiling this is the effect asked for. The composed draw takes a hard
hole and pays for it with easy ones: more expert holes, the same ceiling, and the
easiest holes on up to 42% of courses. A uniform draw mostly avoids the hard holes.

When the shape of the course floats, the lean can be large. See the par 6 figures below.

### Mixing the lean out

`Problem.mix(picks, rng, steps)` takes the course the search found and tries `steps`
small random changes, keeping each only if every rule still holds. `generate` does not
call it. `Problem.draw(rng, mix=N)` and `golf-randomize survey --mix N` do.

A step is one of three changes:

| Change | Share of steps | What it reaches |
|---|---:|---|
| Two slots swap holes | 25% | Another order of the same holes |
| One slot takes another hole of the pool | 25% | Another hole of the same kind |
| Two slots take other holes at once | 50% | Another mix of pars under a fixed total, where one hole going up must be met by one coming down |

A new hole is drawn as a family and then one of its holes. Each change is as likely to
be tried as the change that undoes it, so with enough steps every course the changes
can reach is equally likely, counting a hole with a twin for no more than one without.
That is the distribution `survey --uniform` deals. With a `weight` rule a course is
likelier by the product of its holes' factors, which gives the factor a meaning that
does not have to be measured.

Against the uniform deal, 4,000 courses each (2,000 for no cap). The par 6 rules here
are par 72 exactly with four or five par 3s, nine or ten par 4s and at most one par 6:

| Rules | Measure | Search alone | 200 steps | 400 | 800 | 1,600 | Uniform |
|---|---|---:|---:|---:|---:|---:|---:|
| Standard, no cap | predicted over par | +7.73 | +7.75 | +7.70 | +7.88 | +7.84 | +7.87 |
| Standard, cap 1 | courses with 2 experts | 73.5% | 51.1% | 51.9% | 51.0% | 50.6% | 51.1% |
| | predicted over par | +5.66 | +5.36 | +5.36 | +5.29 | +5.28 | +5.32 |
| Each nine at most +3.0 | courses with 3 or more experts | 43.6% | 18.9% | 15.6% | 14.5% | 14.2% | 14.6% |
| | predicted over par | +5.07 | +4.14 | +4.13 | +4.13 | +4.17 | +4.14 |
| Par 72, a par 6 allowed | courses with a par 6 | 57.0% | 49.7% | 44.0% | 40.9% | 38.2% | 40.0% |

At 4,000 courses a share is good to about a point either way. With two-slot changes
at a fifth of the steps the par 6 row needed 1,600 steps, and at half it needs 800.

Most presets are too rare for the uniform deal to finish, so for those the yardstick is
the same pass run for 6,400 steps. The figure is the root-mean-square difference in how
often each hole of the pool is on a course, in points; two samples of 4,000 from one
distribution differ by about 0.7.

| Preset | Steps kept | Search alone | 400 steps | 800 | 1,600 |
|---|---:|---:|---:|---:|---:|
| `par_3_course` | 26% | 2.0 | 0.8 | 0.5 | 0.4 |
| `forward_tees` | 22% | 7.7 | 2.3 | 1.1 | 0.7 |
| `world_tour` | 13% | 3.3 | 1.6 | 1.1 | 0.8 |
| `expert_gauntlet` | 20% | 3.3 | 2.1 | 1.5 | 0.9 |
| `grand_finale` | 20% | 2.2 | 1.6 | 1.3 | 1.1 |
| `even_nines` | 12% | 2.7 | 1.9 | 1.5 | 1.1 |
| `ladder` | 2% | 4.0 | 3.0 | 2.4 | 1.7 |
| `running_order` | 4% | 3.8 | 3.1 | 2.6 | 1.9 |
| `tips` | 20% | 13.1 | 6.9 | 4.7 | 2.4 |

- **Loose rules settle in a few hundred steps.** The standard course and the per-nine
  ceiling keep about a quarter of the steps tried.
- **Rules with an order to keep settle slowly.** `ladder` keeps one step in fifty,
  because most changes break the order.
- **A course in the tail starts far away.** `tips` keeps one step in five, but the
  search's courses sit hard against the 7,900-yard floor and take thousands of steps
  to spread out.
- **6,400 steps is not shown to be enough for these.** `even_nines` was still drifting
  between 1,600 and 6,400, from +5.44 over par to +5.26.

One step costs 13 to 20 microseconds on one core, less for a preset with few rules:

| Steps | Added to a draw | A standard course in all |
|---:|---:|---:|
| 0 | 0 | 9 ms |
| 200 | 3 ms | 12 ms |
| 800 | 10 to 16 ms | about 20 ms |
| 6,400 | 85 to 130 ms | about 95 ms |

A course with one answer, such as a vanilla course, comes back unchanged.

Not tried: a number of steps chosen from how many are being kept, changes that know the
rules (moving a hole along an ordered course), and dealing courses blind for a short
while before searching. The blind deal is exactly uniform, and on one core it takes
2.6 ms for a standard course with no cap, 11 ms with a cap of 1 and 20 ms with the
per-nine ceiling, against 9 to 12 ms for the search.

## A par 3 course

The rule is one line. The supply is the constraint, as expected.

| Sources | Par 3 families | Result |
|---|---:|---|
| Both ROMs | 23 | Generates. A family is on 78% of courses, a hole with a twin on about 39% |
| US only | 12 | Refused: "the pool has 12 holes matching {"par": 3}, in 12 families" |
| JP only | 20 | Generates. A course is 18 of the 20, so most families are on 95% of courses |

A family's share is 18 over the families that offer a par 3. `golf-derive` puts a short
in its base's family, so each short of a par 4 or 5 adds a family. A synthetic pool of
twelve par 3s and eight par 5s with shorts generates par 3 courses, and a standard
course from it never holds a base and its short together
(`test_a_short_shares_a_family_with_its_base`).

The catalog has no derived hole yet, so the preset's `include: ["derived"]` changes
nothing today.

Two things the rules add to that picture:

- **Keeping shorts out of a standard course** is `{"rule": "exclude", "where": {"tag":
  "short"}}`, for a seed that wants other derived holes. ADR 0023 names that as its
  reason to revisit.
- **Spreading the distances** is a `gap` or a `sum`, when a run of 120-yard holes turns
  out to be a problem.

## Par 6, through the same rules

The six longest holes were re-parred to 6 in memory, to put the par 6 note's questions
to this model. 2,000 courses each, no expert cap. "Long" is a par 5 or 6.

| Rules | Courses with a par 6 | Course par | A par 6 hole's share |
|---|---:|---|---:|
| 4 short / 10 mid / 4 long, par floats | 70% | 72 to 76 | 16.4% |
| The same, at most one par 6 | 67% | 72 or 73 | 11.2% |
| The same, par 6s weighted 0.25 | 27% | 72 or 73 | 4.5% |
| Par 72 exactly, profiles 4/10/4/0 or 5/9/3/1 | 16% | 72 | 2.7% |

- **The first row is the family model at work.** The note's table assumed 6 par 6s among
  32 long holes. The draw picks families, and the six are 6 of 25 long families, so a
  course has no par 6 30% of the time, not 42%.
- **The last row shows the lean.** A uniform draw over the same rules has a par 6 on 36%
  of courses. The composed draw gives 16%, because the 5/9/3/1 profile needs two unlikely
  choices to line up.
- **So a stated frequency is a request's job.** ADR 0016's `one_of` over "no par 6" and
  "exactly one" at 3 to 1 says "one seed in four" exactly. A weight gets near it by
  trial.

The note's three generation models are all rule lists here: fixed profiles are
`par_counts`, shape classes are counts by selector (`shape_by_distance`), and
conditioning on total par is `total_par`.

## When there is no course

- **Proved impossible.** The message names the smallest set of rules found that cannot
  hold together, with what the pool offers each. Rules that are not part of the clash
  are left out.
- **Gave up.** The message says so, lists the rules, and names each rule a course is
  found without.

```
the pool (144 holes in 108 families) has no course that keeps these rules together:
  - {"rule":"standard"}
      (the pool has 32 holes matching {"par": 3}, in 23 families)
      (the pool has 80 holes matching {"par": 4}, in 60 families)
      (the pool has 32 holes matching {"par": 5}, in 25 families)
  - {"pars":[4],"rule":"no_consecutive"}
```

600 random rule sets, each a base shape and up to four random rules, many of them far
into the tail of what the pool gives:

| Outcome | Sets | Median | 99th percentile | Slowest |
|---|---:|---:|---:|---:|
| Found a course | 473 | 15 ms | 475 ms | 2.7 s |
| Proved impossible | 116 | 21 ms | 1.6 s | 3.1 s |
| Gave up | 11 | 3.4 s | | 6.3 s |

The eleven are stacks of tail rules, such as a par 70 with two to five holes from each
vanilla course and at least 8,080 yards. Where the edge is sharp the search finds it:
with the expert cap, a standard course of 8,400 yards is found in 50 ms and one of 8,500
proved impossible in 60 ms.

## What it costs

- **Speed.** About 10 ms to draw a standard course and 3 ms to compile its rules,
  against 0.6 to 2.7 ms for the old draw.
- **Code.** The solver is 1,040 lines, composing 925 and the rules 610. The draw they
  replace is 170, with 140 for layouts.
- **A formula.** A family's share was slots over families. Now it is measured.
- **One seed, one layout.** The layout had its own stream, so a seed drew the same
  layout from any pool. A test held that and was dropped.
- **A search can give up.** The old draw could not. On a standard course it has not: no
  failure in the 24,000 drawn for this report.

One thing was gained without asking. The old capped draw needed a pool that could fill
the course with no expert hole at all. The search has no such need, and the test of it
now tests the opposite.

Four existing tests changed for the draw: the schema number, the shared layout, two
error messages and the capped draw above. The tests of `draw_rule` (the manifest's, the
CLI's flags, the site's form and seed page) now test the expert cap as a rule. The
build's golden-hash test gives its manifests the old draw's holes, and its hashes are
unchanged.

## Open questions

1. **Where do `par` and family repeats live?** Both are rules now. The expert cap is
   in the list: a list holds what it says, and the default course is the one place a
   cap comes from unasked. `par` is still a field, inert beside a list, and the family
   rule is still added to every list.
2. **Is the lean toward limits wanted?** It matches the old capped draw and suits the
   per-nine ceiling. It is not what "every valid course equally likely" would mean.
3. **How does ADR 0016 take this?** `request.py` is a stand-in for its unweighted half.
   The `about` key and constraints inside `one_of` are both new to it.
4. **Where does `over_par` come from?** The rules read `data/difficulty/holes.json`. A
   hole with no row, which is every derived hole, cannot go where a rule sums it.
5. **Which rules are public?** The general ones are powerful and easy to get wrong. The
   named ones could be the whole of what a request may use.
6. **What does a seed record?** Its constraints, as written. Not which preset they came
   from, so "rounds on vanilla US" means recognizing the list until a request block
   holds a name.

Left alone on purpose:

- **The site.** Its form sends a standard course with no expert cap or a cap of 0 or
  1, and no other rule. Its select, its strings and the seed page's row are still named
  for the draw rule. `server/builder.py` still builds every
  layout at start-up, which generation no longer reads, and the seed page still shows
  `settings.par` as the target.
- **`legacy_draw.py` and `layout.py`.** The first exists for comparison. The second
  still supplies the standard counts.
- **`docs/randomizer.md`**, which is jdharms's own and describes the layout stage as
  the design.

## Trying it

```bash
golf-randomize rules
golf-randomize generate --preset par_3_course -o par3.json
golf-randomize generate --preset vanilla_jp_uk --seed week-6 -o uk.json
golf-randomize build nes_open_us.nes par3.json -o par3.nes
golf-randomize survey --preset compensating_nines -n 1000
golf-randomize survey --preset natural_par --uniform -n 1000
golf-randomize survey --legacy -n 1000
golf-randomize survey --preset compensating_nines --mix 800 -n 1000
golf-randomize generate --request my_settings.json
```
