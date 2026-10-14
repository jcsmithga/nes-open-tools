+++
status = "proposed"
date = 2026-10-14
area = "randomizer"
permanent = false
revisit_when = "players want a course shape the five modes and a request cannot give them, a mode's par mix needs odds a player sets per seed, or new holes make a mode's courses repetitive"
drafted_by = "Claude"
supersedes = []
+++

# Course modes are named rules that roll their par mix, and transforms are a chance per hole

## Context

The site has offered one kind of course: a standard one of par 70, 71 or 72, with no cap
on expert holes or a cap of 0 or 1 a nine. The `generation-spike` branch replaced the
layout-first draw with a list of rules that a search keeps
(`docs/generation_rewrite.md`), and with 35 requests in `data/presets/` showed what
those rules can say. ADR 0024 then made a seed its stored artifact, so how generation
works can change without any stored seed depending on it.

Players asked for five kinds of course: a par 3 course, an executive course, a standard
par 72, a course whose par is whatever its holes come to, and a vanilla course played as
the cartridge has it. Each keeps the expert cap: none, 1 or 2 a nine, or no limit.

> What we found is that people enjoy playing "non-standard" par courses, but there's a
> bias towards *choosing* to roll a non-standard par course.

So the par of a natural par course should be rolled, not chosen, and the odds of each
par should be ones the project sets.

The spike's requests left those odds to the pool. In the search, a slot takes a kind of
hole in proportion to the families of that kind still open to it, so a range of par
counts is filled from wherever the supply is deepest:

- **Natural par** (par 68 to 76, no two par 3s or par 5s in a row) puts a par 3, 4 or 5
  on a slot about 20%, 62% and 18% of the time over 1,000 seeds: the pool's 23 par 3
  families against its par 4s and par 5s. A seed that included many new par 3s, which
  the community is expected to make for par 3 courses, would slide toward par 68.
- **An executive course** of 2 to 6 par 4s and no par 5s, the nines balanced, came out
  with 6 par 4s on 85% of 300 seeds and 5 on the rest. 2, 3 and 4 never came up.

The obvious fix, a weighted choice over the par mixes in the request (ADR 0016), does
not compose. A `one_of` replaces the whole `constraints` field, so natural par with an
expert cap of 0 would repeat the cap in each of 70 alternatives, and a player choosing
between natural par and a standard course in one settings request would need a third
level of weighting, which ADR 0016 refuses.

ADR 0016 already says where such randomness belongs: "Randomness that is part of a rule
stays in generation," as the hole draw does.

Players also want two hole transforms (`docs/hole_transforms.md`), mirroring and
shuffled hazards, which no generated seed has had.

## Decision

### Modes

A mode is a named rule (`golf/randomizer/rules.py`). The site's presets are lists of
rules, and a player's request writes the same lists:

| Mode | Rules |
|---|---|
| Standard | `{"rule": "standard"}`, par 72 |
| Natural par | `{"rule": "natural_par"}` |
| Executive | `{"rule": "executive"}` |
| Par 3 | `{"rule": "par_3"}` |
| Vanilla course | `{"rule": "vanilla_course", "course": "<course>"}` |

Every mode but a vanilla course adds `{"rule": "expert_cap", "per_nine": N}` for a cap,
and leaves it out for no limit. A vanilla course has the expert holes its course has, so
it takes no cap, and its preset sets `sources` to its course's ROM.

### The rules a request may use

A seed's `constraints`, from the form or from a request a player uploads, hold exactly
one of the five mode rules and at most one `expert_cap`, and a vanilla course takes no
cap. Settings that break this are refused when they are made, so a request is judged
when it is submitted rather than failing as a seed. The rule that keeps families apart is added to
every list, as now. Players combine modes, caps and the other settings; they do not
write new shapes of course. The spike's format for that was opaque to anyone but a
software engineer.

The rules the modes are written in, `count`, `compare` and `limit`, and the shorthands
over them (`par_counts`, `balanced_nines`, `no_consecutive`, `slot`), stay as internal
building blocks that a mode rule expands into. The spike's other rules (`sum`, `step`,
`gap`, `weight`, `total_par`, `highest`, `lowest`, `exclude`, `require`, `pin`), the
selector fields and numbers only they read, and the solver machinery that served only
them, are removed. So is `data/presets/`: "preset" means only the site's named
choices, and a mode rule says in a line what a preset file said.

A standard or natural par course never finishes on a par 3, which is rare on real
courses. A par 3 course and an executive course are mostly par 3s, and a vanilla course
plays its holes as they are, so neither takes the rule.

### Rolled rules

`natural_par` and `executive` are **rolled rules**. Generation rolls each one's par mix
from its own stream, `stream(prng_seed, "par_mix")`, in list order, before any hole is
chosen. The rolled rule then stands for the fixed rules of that mix, and the search
composes the course as it does for any list.

- **`natural_par`** gives each slot a par 3, 4 or 5 with odds 4, 10 and 4 in 18, then
  keeps the layouts of par 68 to 76 with no two par 3s or par 5s in a row. The roll is
  over the mixes those layouts make, each as likely as the layouts that make it: 70
  mixes, par 72 on 20.3% of seeds and par 68 or 76 on 2.7% each, which share a slot 17%,
  66% and 17% between the pars. The rule then stands for that mix's `par_counts`,
  `{"rule": "no_consecutive", "pars": [3, 5]}` and no par 3 on the 18th. Where the pars
  go is not part of the odds: the 18th is kept off a par 3 when the course is laid out,
  as a standard course's is, and every mix has room for it. The odds are computed from the rule's
  definition, not measured from the pool, so no change to the catalog moves them.
- **`executive`** rolls a number of par 4s from 2 to 6, each as likely as the others,
  and stands for `par_counts` of that many par 4s, the rest par 3s and no par 5s, and
  `balanced_nines`. Its nines are then within a stroke of each other. The odds are a
  starting point to tune with `golf-randomize survey`.

A rolled rule's definition, odds included, is part of what its name means. Changing
the odds is a new version, `natural_par@2`, beside the old one until nothing generates
it, as with transforms.

Settings hold rolled rules by name, so the manifest's `settings` say `natural_par` and
the seed page and stats can tell a seed's mode from them. The mix that was rolled is
the course's pars.

### Every course is mixed after the search

`generate` runs `Problem.mix` on the course the search finds, for a fixed number of
steps (800 to start with). A step is a small random change, kept only if every rule
still holds, and each change is as likely to be tried as the one that undoes it, so the
walk tends toward every course that keeps the rules being equally likely
(`docs/generation_rewrite.md`, **Mixing the lean out**).

The search alone leans toward the limits of the rules. With the expert cap of 1, 73% of
standard courses have exactly 2 expert holes, where 51% would if every valid course were
as likely as any other. 800 steps bring the standard course, with or without the cap,
to the uniform figures, and cost about 10 ms a seed. A rolled rule's mix is fixed before
the search, so the mix changes the holes and their order, never the pars a mode rolled.

The step count is fixed, not a time, so the same inputs give the same course on any
machine. Rules with an order to keep, such as each hole longer than the last, settle
far more slowly (`ladder` was still moving after 1,600 steps). No rule a request may use
has one.

### Par and the default course

`settings.par` is removed, and with it par 70 and 71: natural par replaces them.
`standard` is par 72 and takes no argument. `constraints` is never `null`. The default settings hold the standard par 72 with an
expert cap of 1, written out. Schema 5 has not been issued, so it changes in place.

### Transforms

Settings gain two fields, each a whole-number percent from 0 to 100:

| Field | Default | The transform it adds to a slot |
|---|---|---|
| `mirror_chance` | 0 | `mirror@1` |
| `hazard_chance` | 0 | `hazards@1:<seed>`, the style calibrated to vanilla's share of water |

Generation rolls each slot against each chance on its own stream (`"mirror"`,
`"hazards"`), after the holes are chosen. A hazard seed comes from the `"hazards"`
stream. A slot that gets both is mirrored first. A hole the transform leaves unchanged,
such as one with no hazards, still counts as rolled. The expert cap and the difficulty
figures read the untransformed hole.

A chance is per hole, not a share of the course. At 25%, 94% of seeds transform 2 to 8
holes, and 1 in 180 transforms none.

### The site

- **The generate page** keeps a short form: a mode, a course (shown for Vanilla), the
  expert cap (none, 1, 2, no limit; not for Vanilla), sources, the two transform
  chances at 0, 25, 50, 75 and 100%, and the wind, music and club settings it has now.
  The server turns the form into a request (ADR 0016) and resolves it as any other.
  The words for each chance are in the strings catalog.
- **A separate page** takes a request file a player uploads, for mystery seeds
  (ADR 0016). It is the only way to send a request the form cannot express.
- **A par 3 course** from the form includes derived holes, the forward tees of longer
  holes, since the par 3 course is the mode short of holes.
- **The upload** is at most 16 KB and parsed strictly; a request that rolls settings no
  course can be made from is refused with what was rolled, and not rolled again.
- **Combinations that cannot be generated** are refused by generation, not by the form,
  such as a par 3 course from the NES Open holes alone. The holes that can fill a mode
  will grow with derived and community holes, and form logic would have to follow them.

## Rejected alternatives

- **A weighted choice over par mixes in the request.** It does not compose with other
  rules, and needs a third level of weighting to sit beside another mode (Context).
- **Drawing a pool at 4 par 3s to 10 par 4s to 4 par 5s, then the course from it.** It
  fixes natural par's odds against new holes too, but leaves some of the pool out of
  every seed, and gives executive nothing.
- **A rolled rule's mix written into the resolved settings as `par_counts`.** Settings
  would stay free of rolled rules, but would no longer say which mode made the seed, and
  stats would have to recognize a mode from its pars or the request block.
- **Request aliases for presets** (`{"preset": "natural_par"}`). A preset carries its
  expert cap, so a player would at once want to override part of one. Named rules are
  the unit a player combines.
- **Per-mode checks of sources in the form.** See **The site**.
- **The spike's whole vocabulary in requests.** A request could describe any course its
  rules can say, from a ladder of yardages to a palindrome of pars, but only engineers
  would write one, and its rules took most of the solver's machinery. Players asked for
  modes, not a language.
- **A transform share of the course** (exactly a quarter of the holes). It reads as a
  guarantee, which players asked not to be given.
- **The search alone, as the site has drawn until now.** A standard course with the
  expert cap of 1 has exactly 2 expert holes on 73% of seeds, a bias players do not
  want.
- **Mixing until the walk settles, measured per seed.** Fewer steps for loose rules,
  more for ordered ones, but a seed's cost and course would depend on a measure taken
  during the walk. A fixed count is simpler, and enough for every mode.

## Consequences

- A player's request can name any mode in a line, with or without an expert cap.
- New holes change which holes fill a mode's pars, never its mix.
- Two modes in one list, such as `natural_par` with `standard`, are refused with the
  settings, before generation.
- Settings gain rules that are not fixed until generation, the second kind of
  randomness in settings after `music: "random"`.
- A standard seed's expert holes spread out: fewer courses at the cap (51% at 2, not
  73%), and slightly fewer predicted strokes over par (+5.3 against +5.7 with the cap
  of 1). With the modes and the new draw, generation changes enough to bump
  `generator_version`.
- Generation takes about 10 ms longer a seed, which the site's server can afford.
- Of the spike's 35 preset files, none survives: the modes are named rules, and the
  site's presets are built from them. The figures the spike measured on the others stay
  in `docs/generation_rewrite.md` as its record.
- A course shape players want later, such as a par 6 or a signature 18th, is a new
  named rule, and may bring back some of what was removed.
- `natural_par` and `executive` move with their own versions, apart from
  `generator_version`.
- Transformed holes reach players for the first time. The rangefinder's renders show
  each hole unmirrored, and nothing checks a shuffled hazard's playability
  (`docs/hole_transforms.md`). The seed page shows which holes were transformed.
- The site's form loses `par` and the draw rule select, and the seed page's draw rule
  row becomes the mode.

## Sources

- `docs/generation_rewrite.md`, `docs/manifest.md`, `docs/hole_transforms.md`,
  `golf/randomizer/rules.py`, `golf/randomizer/compose.py`
- ADR 0016, ADR 0023, ADR 0024
- Claude Code session 2026-10-14 `018V5k6x`: jdharms's five modes and their expert
  caps, natural par replacing par 70 and 71, the mode and course as two selects, transform
  chances rolled per hole, impossible combinations left to generation, a separate page
  for request uploads, named rules for the par mixes, and the mixing pass on, since the
  search's lean to exactly 2 expert holes on a standard course is unwanted; requests
  limited to the modes, since the rule format is opaque to players; `data/presets/`
  removed, since "preset" means the site's choices. The surveys
  above, with and without mixing, and the computed natural par odds were run in that
  session.
