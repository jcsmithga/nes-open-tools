# Thoughts on Par 6

> **Note**: This document was written by Codex after a high-level design discussion.

This is a design note, not an implementation plan. Nothing here commits the randomizer
to supporting par 6, identifies any hole as a par 6, or chooses a generation algorithm.
Its purpose is to make the product decisions visible before an implementation makes them
accidentally.

The immediate question comes from several unusually long holes in Mario Open. The broader
question is how course generation should behave when the pool's supply of different kinds
of holes no longer matches the traditional four-par-3, ten-par-4, four-par-5 course. Par 6
is only the first plausible way to expose that issue. Community holes, alternate tees and
future transformations can expose it too.

Code under discussion: `golf/randomizer/layout.py`, `golf/randomizer/legacy_draw.py`,
`golf/randomizer/pool.py`. Background: [randomizer.md](randomizer.md),
[catalog.md](catalog.md), [manifest.md](manifest.md).

The generation this note calls today's is the layout-first draw of generator version 2,
which `golf/randomizer/legacy_draw.py` holds. [generation_rewrite.md](generation_rewrite.md)
reports a spike of the constrained selection the last section points toward, and puts
this note's par 6 scenario through it.

## Before generation: what would make a hole par 6?

Distance alone is not enough. Re-parring a hole should follow play data showing both a
statistically reliable and practically meaningful difference from the par-5 population.
A reasonable standard would be evidence that the hole's expected strokes are more than
one full stroke above the expected strokes on ordinary par 5s, with enough observations
that sampling noise, player mix and playing conditions are not plausible explanations.

The exact analysis is a later question, but the important distinction is settled here:
**par is a scoring classification, not a synonym for length or novelty.** The long holes
are candidates for study, not candidates for automatic promotion.

The distance outliers that motivate the question are:

| hole | yards |
|---|---:|
| jp_uk/18 | 838 |
| jp_uk/14 | 778 |
| jp_hawaii/05 | 773 |
| jp_uk/09 | 766 |
| jp_hawaii/14 | 762 |
| jp_hawaii/18 | 750 |

There is a visible break after these six: the next-longest vanilla par 5 is 700 yards.
That makes six a useful scenario for analysis, not a conclusion about the holes.

## Three concepts currently sharing one number

Today `par` does three jobs at once:

| concept | question it answers | example |
|---|---|---|
| **Scoring par** | What score counts as par on this hole? | A hole is par 6 because its demonstrated scoring difficulty warrants it. |
| **Course shape** | What kind of playing rhythm should the round have? | Four short, ten middle and four long holes, with no consecutive long holes. |
| **Selection policy** | How often should this content appear across seeds? | A monster hole appears in one seed out of four, or each family gets comparable exposure. |

The three concepts interact, but they should not determine one another accidentally.

### Scoring par

Scoring par belongs to the hole. It controls the number displayed by the game, relative-
to-par scoring and the course total. If evidence says that a hole is a par 6, correcting
that metadata should not by itself decide whether the hole appears in 10%, 50% or 75% of
seeds.

### Course shape

Shape describes the experience of playing eighteen holes. A 700-yard par 5 and an
838-yard par 6 are both long holes for pacing purposes. Putting them back to back creates
the experience that a no-consecutive-long-holes rule is meant to prevent, regardless of
the digits on the scorecard.

The inverse can happen as community content arrives. A short, attackable par 4 may fill
the same role in the course's rhythm as a conventional par 3. This suggests that
`short`/`mid`/`long`, or some similarly explicit classification, is a better vocabulary
for shape predicates than par itself.

### Selection policy

Selection policy controls exposure, variety and composition across seeds. It answers
questions such as:

- Should every underlying hole design receive comparable exposure?
- Should memorable or extreme holes appear less often than ordinary holes?
- Should a course contain at most one monster hole?
- Should every course contain community content or holes from both source ROMs?
- When filters shrink one part of the pool, should the remaining holes become more common
  or should the course's composition change?

None of those answers follows from whether a hole's correct scoring par is 5 or 6.

## How generation works today

Generation is layout-first. `choose_layout` draws uniformly from `layouts(par)`, an
enumerated set of par sequences with fixed counts that pass the shape predicates. For par
72, `COUNTS` requires four 3s, ten 4s and four 5s. `draw_holes` then fills each slot from
a distinct family that offers that par.

The eight vanilla courses all have the same 4/10/4 profile, giving the catalog 32 par 3s,
80 par 4s and 32 par 5s. Ignoring families for a moment, supply and demand have the same
ratio:

| par | catalog entries | slots per seed | entry appearance rate |
|---|---:|---:|---:|
| 3 | 32 | 4 | 12.5% |
| 4 | 80 | 10 | 12.5% |
| 5 | 32 | 4 | 12.5% |

This equality is not something the generator deliberately maintains. It falls out of the
source courses and the generated layout sharing the same profile.

It is also not literally true for every current catalog entry. Generation samples
families first and then a member of the chosen family. The current curation groups
`nes_uk/01` and `jp_japan/01` into one family, so the two variants divide that family's
exposure. The useful invariant is therefore closer to **equal exposure among comparable
families within a par**, not equal exposure among catalog entries.

For families that each offer exactly one par and have one member, the appearance rate is
exactly:

```
slots of that par / families offering that par
```

Mixed-par and multi-member families make the entry-level probabilities more complicated.

## Why a scarce par breaks layout-first generation

If the layout says “par 6 here,” only a par-6 family can fill the slot. With one par-6
family, every layout containing a 6 contains that family. No weighting inside
`draw_holes` can change that fact: by the time hole selection begins, the important
decision has already been made.

The same ordering creates a feasibility problem. `choose_layout` does not inspect the
pool; `_matchable` discovers afterward whether distinct families can fill it. A layout
asking for two par 6s cannot be filled from a pool containing one par-6 family.

Two problems that can sound similar should remain separate:

- **Over-exposure:** a hole or family appears in too many seeds. This is a selection-policy
  problem.
- **No variety:** every par 6 encountered is the same hole. This is a supply fact. A
  sampler can show it less often, but only more suitable holes can make it varied.

## The unavoidable tradeoff at fixed par 72

Assume for analysis that six of the 32 vanilla par 5s become par 6. The entry counts are
then 32 / 80 / 26 / 6.

If every catalog entry appeared at the same 12.5% rate, the expected course profile would
be 4 / 10 / 3.25 / 0.75 and its expected total would be:

```
3(4) + 4(10) + 5(3.25) + 6(0.75) = 72.75
```

Therefore exact equal entry exposure and an exactly par-72 course cannot both hold. The
same proof applies to equal family exposure when the family population has an average par
above 4.

Holding the total at 72 requires paying back each added stroke somewhere else. Replacing
a par 4 with a par 3 is one way, but it changes short-hole frequency and the course's
shape. That may be acceptable; it should be recognized as a product decision rather than
an arithmetic necessity with no player-facing consequence.

## Product decisions to make before choosing a sampler

### 1. What is the fairness unit?

The likely candidates are catalog entries, lineages, families or distinct playing
experiences. They are not interchangeable.

Suppose one underlying design has two variants while another has one:

| policy | twin A | twin B | singleton | twin design collectively |
|---|---:|---:|---:|---:|
| entry-fair | 10% | 10% | 10% | 20% |
| family-fair | 5% | 5% | 10% | 10% |

The current family model deliberately favors the second interpretation: creating or
discovering a variant should not make the underlying design dominate generation. That is
probably the right default, but it should be named explicitly.

### 2. How often should players encounter the par-6 experience?

Six holes are only 4.2% of a 144-hole catalog. Equal entry exposure sounds modest, but it
produces 0.75 par-6 slots per course on average. Depending on the sampler, that can put a
par 6 in roughly half or more of all seeds.

A normal par 4 can recur at that marginal rate without being noticed. An 838-yard par 6
is salient. Players will remember it, discuss it and perceive the category as repeating.
The product question may therefore be less “how often should each of six entries appear?”
and more:

> How frequently should a player encounter the experience of a par-6 monster?

Possible answers include proportional exposure, a novelty budget, a hard cap of one per
course, or an intentionally low course-level probability. Play feedback should inform
this independently of the statistical case for assigning par 6.

### 3. Is total par an input, a narrow range or an output?

If course shape remains four short / ten mid / four long and long slots can contain par 5
or par 6, the resulting total naturally floats. With six par 6s among 32 long holes, a
uniform four-hole draw gives:

| par 6s drawn | 0 | 1 | 2 | 3 | 4 |
|---|---:|---:|---:|---:|---:|
| probability | 41.6% | 43.4% | 13.6% | 1.4% | 0.04% |
| course par | 72 | 73 | 74 | 75 | 76 |

This preserves traditional course shape and lets the scorecard describe the selected
holes honestly. It also makes a par 6 present in 58.4% of seeds, which may be too common
for the desired experience.

There can still be good reasons to require exactly 72: player expectations, league
reporting, aesthetics or the meaning of a generation setting. Other reasonable contracts
are “natural par,” a 72–73 range, or a choice between traditional and natural modes. The
important point is to choose the contract deliberately.

### 4. Which properties define course shape?

The present rules balance each par across the nines and forbid consecutive par 3s and par
5s. If shape classes become explicit, questions include:

- Are there always exactly four short and four long holes?
- Should every nine contain two of each?
- Does `long` include every par 6 and every conventional par 5?
- Can an unusual hole override the class normally implied by its par?
- Are monster holes merely long, or a separate class with their own cap?

This classification may be useful even if par 6 never ships.

### 5. What composition guarantees matter?

Community holes create an independent axis. Potential policies include minimum or maximum
community content, representation from both source ROMs, author diversity, and limits on
particular tags. These rules need not be encoded as new kinds of layout slot; they can be
constraints on selection within a shape.

## Candidate generation models

These are families of policy, not three mutually exclusive implementations.

### Fixed par profiles

Extend the current count table with one or more par-6 profiles and choose among them with
a configured probability. For example, a par-72 profile containing one 6 must also differ
somewhere below par 4. Starting from 4/10/4, one possible transformed profile is
5/9/3/1.

This is easy to explain and keeps the total pinned. Its drawbacks are that the probability
must be chosen by hand, supply changes require reconsidering it, and adding another scarce
category multiplies the profiles. Arrangement also needs care: demoting an arbitrary par
4 to par 3 can create consecutive short holes, so the existing predicates do not survive
the transformation automatically.

This model is appropriate if the product policy really is a small set of traditional
profiles. It is not inherently wrong merely because it is hand-authored.

### Shape-class layouts

Generate a layout of short, mid and long slots, then select holes of the matching class.
Scoring par is read from each selected hole and the course total follows from the result.

This cleanly separates scoring from pacing, automatically adapts to par 6 and gives the
predicates the vocabulary they appear to want. It does not, by itself, answer how often a
monster should occur or how community content should be represented. Those remain
selection policies layered onto the class layout.

### Selection conditioned on total par

Select eighteen distinct families subject to total par 72, then arrange the selected
holes under the shape predicates. This makes feasibility supply-aware and allows many
composition constraints to be expressed in one selection stage.

For a simplified pool in which every family has one member and one par, an unordered par
profile with counts `c[p]` has weight:

```
product(combinations(families_with_par[p], c[p]))
```

Sampling profiles in proportion to that weight, then sampling families within each par,
is equivalent to uniformly selecting unordered family sets conditioned on total par. It
has bounded runtime and can report impossibility directly; rejection sampling is not
required.

This is a useful neutral baseline, but it is not “equal exposure for free.” Conditioning
on total par changes marginal appearance rates. With a simplified 32 / 80 / 26 / 6 entry
pool and no family or arrangement complications, it produces:

| entries re-parred to 6 | par 3 | par 4 | par 5 | par 6 | P(course has a 6) |
|---:|---:|---:|---:|---:|---:|
| 0 | 12.2% | 12.8% | 12.2% | — | 0% |
| 1 | 12.3% | 12.8% | 12.1% | 10.2% | 10.2% |
| 2 | 12.5% | 12.8% | 12.0% | 10.0% | 19.5% |
| 3 | 12.6% | 12.8% | 11.9% | 9.9% | 28.1% |
| 4 | 12.8% | 12.8% | 11.8% | 9.8% | 36.0% |
| 5 | 12.9% | 12.8% | 11.7% | 9.7% | 43.2% |
| 6 | 13.0% | 12.8% | 11.6% | 9.6% | 49.8% |

Those probabilities are mathematically coherent, supply-responsive and perhaps desirable.
They are nevertheless a policy outcome: fixed par favors some categories over others.

### A combined model

The most general direction separates the three concepts explicitly:

1. Build the actual pool of eligible families.
2. Classify holes for course shape independently of scoring par.
3. Enumerate or count feasible aggregate profiles under the chosen total-par, shape and
   composition constraints.
4. Weight those profiles according to a documented selection policy.
5. Select distinct families and then arrange them under the shape predicates.

This is a combination of shape-class layouts and supply-aware selection. “Uniform among
conditioned family sets” can be its default weighting if that behavior is wanted, but
uniformity is the implementation of a policy rather than the policy itself. A novelty cap
or desired monster frequency can instead be part of the profile weight or constraints.

The combined model is the most adaptable to community content. It is also more machinery
than a single par-6 feature necessarily deserves. The right implementation depends on
which product decisions survive playtesting.

## What does “uniform course” mean?

Any future design should name its sample space. At least three plausible distributions
can be called uniform:

1. Uniform over par or shape layouts, followed by a hole draw.
2. Uniform over unordered sets of eighteen families, followed by an arrangement.
3. Uniform over fully ordered eighteen-hole courses.

They are different. In particular, profiles have different numbers of legal arrangements.
If a profile is weighted only by the product of binomial coefficients, unordered family
sets are equally likely. If the goal is uniform ordered courses, that weight must also
include the number of valid layouts for the profile.

The third distribution can strongly favor profiles with many possible arrangements. That
is not obviously a player-facing virtue. Treating selection and arrangement as separate
stages may be the clearest policy, but the document and tests should say so.

Existing realism predicates create a related distinction. A profile with no legal
arrangement must be excluded. A profile with one legal arrangement and a profile with a
million can either receive equal set-level treatment or weights proportional to those
arrangement counts. Both are exact samplers for different target distributions.

## Families are the hard edge of the counting model

The simple product-of-combinations formula assumes every family offers exactly one par.
The model already permits a family containing, for example, a par-3 forward tee and a
par-5 version of the same design. Such a family can fill either category but may appear
only once. Independent binomial counts then double-count selections that try to use it in
both categories.

There are three broad responses:

- make single-par families an explicit invariant;
- retain mixed families and use a more general dynamic-programming or matching-based
  counter;
- define the family relationship differently when variants have different gameplay roles.

This decision should not remain accidental. Community content makes mixed-category
families more, not less, plausible.

## Mechanical questions in the ROM

The storage format does not immediately rule out par 6. Per-hole par is a byte in
`TABLE_PAR` at `$DD05`, and `CoursePatch` supports two-digit course totals from 10 through
99. A rudimentary write-and-play test with a par-6 hole showed no visible problem.

That is not sufficient verification. Before committing, the relevant ROM behavior should
be traced and tested thoroughly, including:

- every use of the per-hole par value;
- any lookup table indexed directly or indirectly by par;
- relative-to-par score calculations and their clamps;
- all scorecard, signpost and results displays;
- stroke limits, records, statistics and saved data that remain reachable;
- one- and two-player stroke play over a complete round;
- totals above 72, if variable course par is allowed.

The dangerous case is code that assumes the only possible values are 3, 4 and 5 and uses
par to index a three-entry table. A successful visual smoke test would not necessarily
exercise every such path.

## Code boundaries affected by any eventual design

- `golf/randomizer/layout.py` currently equates layout symbols with par values and caches
  layouts by total par. Shape classes or supply-aware profiles change that contract.
- `golf/randomizer/generate.py` chooses a pool-blind layout before holes and uses matching
  only to reject or protect the subsequent draw. A supply-aware scheme moves feasibility
  earlier and needs a separate PRNG stream for any new profile decision.
- `golf/randomizer/pool.py` makes families the unit of selection and already permits
  multi-par families.
- `golf/randomizer/manifest.py` restricts hole pars to 3, 4 and 5, and validates the par
  setting against the fixed count table.
- `golf/randomizer/catalog.py` includes par in the ROM-bound content hash. Re-parring a
  hole therefore creates a new version of its lineage; it is not a curation edit.
- A change to generation behavior worth telling seeds apart by bumps `GENERATOR_VERSION`,
  which is provenance only (ADR 0024). A manifest schema bump is needed only if the stored
  manifest or its build semantics change.

No generator backward compatibility is required. Stored seeds define themselves; the
generator is free to produce future manifests differently.

## Current direction, without a decision

The most durable conceptual model is:

- evidence determines scoring par;
- desired round pacing determines shape constraints;
- desired cross-seed experience determines selection weights and composition rules;
- total par is either a declared constraint or an honest consequence of the selected
  holes.

That model points toward explicit shape classes plus supply-aware constrained selection.
It does not yet establish that the full combined sampler is worth implementing. A small
set of hand-authored profiles may be the better product if playtesting leads to a simple
policy such as “a par 6 is an occasional one-per-course novelty.”

The decisions that should precede implementation are:

1. Which holes, if any, have enough scoring evidence to warrant par 6?
2. Is the fairness unit a family, and how should variants divide family exposure?
3. What course-level frequency makes par 6 interesting without making it repetitive?
4. Is total par fixed, bounded or derived from the selected holes?
5. What are the actual shape classes and pacing rules?
6. What community, source and novelty composition guarantees are desired?
7. Are mixed-par families supported as a permanent feature?
8. Which sample space, if any, is intended to be uniform?

Once those answers exist, choosing the sampler should be considerably less mysterious.
