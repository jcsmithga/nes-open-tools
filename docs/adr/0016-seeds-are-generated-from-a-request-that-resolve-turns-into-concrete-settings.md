+++
status = "proposed"
date = 2026-10-05
area = "randomizer"
permanent = false
revisit_when = "players want a seed's rolled outcome kept out of its public manifest, or a request needs a dependency between fields that weighted sets of weighted fields cannot express"
drafted_by = "Claude"
supersedes = []
+++

# Seeds are generated from a Request that resolve turns into concrete Settings

## Context

A seed is generated from `Settings` (`golf/randomizer/manifest.py`), and the manifest
records them beside the course they produced. Every settings field is a value the player
chose; the only randomness in a field is `music: "random"`, which generation resolves
against the holes it drew, because Mario Open themes are only eligible on a course with
Mario Open holes.

The settings now hold options players will want rolled, not chosen: a hole draw rule
and two wind profiles, one for speed and one for direction (ADR 0018). Players will also
want odds across them, such as a 30% chance of Mario Open holes, or an 80% chance of
leaving expert holes out and 20% of one per nine.

ALTTP randomizer forks call this a mystery seed: any field of the settings document may
be a weighted choice of legal values, and the generator rolls one. There, players
usually don't learn the outcome until they find it in game. Here, a player should know
the conditions when they step onto the course. Players there keep a tuned settings file
and roll it again and again, and players here will do the same with their requests.

If a weighted value stayed in `Settings`, a seed's record would say what was asked for
but not what was rolled, and stats could not be grouped by the draw rule or wind profiles
a seed actually used. If the site rolled the dice and passed concrete settings on, the
randomness would live outside the package, the CLI would need its own copy, and the
roll would not come from the seed's PRNG.

## Decision

Generation gains a step in front of it:

**Request → resolve → Settings → generate → Course → unfinished ROM → finished ROM**

- **Request**: what was asked for. The site's form, an uploaded request file and the CLI
  all produce one. The site's named choices (presets) are requests the server defines.
- **resolve** (in `golf/randomizer/`): turns a Request into concrete `Settings`, drawing
  from the seed's PRNG seed on its own stream (`stream(prng_seed, "request")`), as the
  layout, hole draw and music already do. The site never rolls.
- **Settings**: concrete and typed, every field a single legal value. They are what
  generation ran with, and what stats group by.

### The PRNG seed is an argument, not part of the request

resolve and generation take the PRNG seed as an argument, drawn fresh when none is
given; the CLI sets one with `--seed`. The seed used is recorded in the manifest's
resolved `settings`, so the roll is on record, and within one release the same request
and seed resolve to it again. A request names
preferences that are rolled again and again, while a PRNG seed belongs to one roll, so
a request is a document a player can keep and reuse unchanged.

### The shape of a request

A request has exactly two levels of weighting, and one of two shapes, told apart by a
top-level `sets` key:

1. **A settings request**: a partial `Settings` object, applied over the defaults. Any
   field may be a weighted choice of legal values. `{}` means all defaults.

   ```json
   {"par": 72, "mercy_point": {"one_of": [
     {"weight": 1, "value": 9},
     {"weight": 1, "value": null}
   ]}}
   ```

2. **A sets request**: `{"sets": [...]}`, a weighted choice of sets. Each set's
   `settings` is a settings request, weighted fields and all.

   ```json
   {"sets": [
     {"weight": 7, "settings": {"sources": ["nes_open_us"]}},
     {"weight": 3, "settings": {
       "sources": ["nes_open_us", "mario_open_jp"],
       "mercy_point": {"one_of": [
         {"weight": 1, "value": 9},
         {"weight": 1, "value": null}
       ]}
     }}
   ]}
   ```

The parsing rules:

- `sets` is a reserved key: no `Settings` field may take the name, and a test holds the
  field names to that. Any key a later request format needs at the top level, such as a
  format version, is reserved the same way.
- A sets request holds only `sets`. A settings field beside it is an error, not a
  default shared by every set: shared defaults would be a third level, and refusing them
  now leaves that open.
- A settings request is checked as strictly as `Settings` is: an unknown key is an
  error. When the unknown key is a near miss of a reserved one (`set`, `Sets`), the
  error asks whether `sets` was meant.
- Objects are partial at every level: a key left out of an object-valued field takes
  that object's default, so `{"clubs": {"max": 10}}` means a bag of at most 10 clubs,
  none banned and no required bag. The manifest loaders stay strict, because a field
  missing from a stored manifest is an error.
- `prng_seed` is refused in a request, with an error pointing at `--seed`. It is a
  `Settings` field, so a manifest's `settings` pasted in as a request would otherwise
  bring the old seed along.
- A weighted field is written `{"one_of": [{"weight": w, "value": v}, ...]}`, not as a
  map from values to weights, because some values are lists or objects (`sources`,
  `clubs`) and can't be keys.
- Weights are non-negative integers and relative: 1, 2 and 2 mean 20%, 40% and 40%.
  Zero is allowed, so an option can be switched off without deleting it, but every
  choice needs at least one positive weight. Integers keep the roll exact across
  platforms.
- resolve picks the set first (a settings request is one set), then each weighted field
  in `Settings` field order, so the same request and seed always resolve the same way
  within one release. That is not a promise across releases, since nothing regenerates
  a stored seed (ADR 0024); a change to resolve bumps `generator_version` only when it
  is worth telling seeds apart by, as with any other change to generation.

### Which randomness belongs where

Randomness that is part of a rule stays in generation. Choosing *which* wind profiles or
draw rule a seed uses is the request's job. The variation the chosen rule produces
(hole-to-hole wind under a profile, the uniform hole draw, picking a track
that fits the course) happens in generation. So `music: "random"` stays a concrete
setting.

### The manifest

The manifest gains a `request` block beside `settings` and `course`, in a schema bump
of its own:

- `request`: the request as submitted, kept so a seed shows how it came to be and the
  site can offer another seed from the same request.
- `settings`: the resolved settings, the PRNG seed used included.
- `course`: unchanged in role, the concrete course the build reads.

Older schemas load without a `request`, and their settings stand for it. As now, an old
manifest serializes back in its original shape (`docs/manifest.md`, **Schema history**).

### Validation and display

A request is checked when it is submitted: every set, and every value of every weighted
field, must be a legal value for its field. A combination that resolves but can't be
generated (a pool too small for the layout, say) fails that seed with an error naming
what was rolled. It is never silently re-rolled, because a re-roll would quietly change
the odds the request asked for.

What players see is a choice the site makes per field and per profile, not a property of
the manifest. The manifest records everything and is public at `/h/<id>.json`, so the
seed page can present a wind profile as a forecast, or show a storm vaguely, but cannot
keep it secret from anyone who reads the JSON.

## Rejected alternatives

- **The site rolls weighted choices and passes concrete Settings to the package.** The
  randomness would live outside the package that owns every other roll, the CLI would
  need a second implementation, and the roll would not come from the seed's PRNG.
- **Weighted values stored in `Settings`, resolved inside generation.** That's how
  `music: "random"` works, but a seed's record would not say which draw rule or wind
  profiles it actually used, and every reader of `settings` would have to understand
  weighted values.
- **ALTTP's form: per-field weighting only, with a value-to-weight map.** Per-field
  rolls are independent, so a request can't say "with Mario Open holes, cap the expert
  holes". Sets cover that. A map also can't hold list or object values as keys.
- **The PRNG seed as a field of the request.** It would sit at the top of every request
  file as `null`, because the site never accepts one. A stored request carrying a seed
  from the CLI would replay that seed when the site offered another seed from it.
- **Settings always wrapped in a top-level `settings` key, beside an optional `sets`.**
  It would make every request file carry a wrapper that only exists to leave room for
  `sets`. Telling the shapes apart by the reserved `sets` key makes any settings
  document a request as it stands.
- **Conditions or expressions between fields.** More than two levels is harder to write
  by hand, to check at submission and to explain. Weighted sets cover the dependencies
  players have asked for so far.
- **Hidden outcomes, as in ALTTP mystery seeds.** Golf conditions are known when you
  step onto the course. A public manifest couldn't hide them anyway.
- **Storing only the resolved Settings.** The seed would lose how it came to be, and
  "another seed like this" would need the player to keep their own request.

## Consequences

- The site's form, request uploads and the CLI share one path into the package, and
  "full control" is a request file rather than a bigger form.
- A request file is reusable as is: it holds no seed, and the simplest one is a plain
  settings object.
- Stats and seed pages read concrete settings, so seeds can be grouped by draw rule,
  wind profiles or sources without parsing weights.
- New settings fields can be weighted without new request syntax, but can never be
  named `sets` or any other reserved key.
- The manifest schema changes for `request`, and its loader keeps reading every schema
  before it.
- A request upload is untrusted input: its size is limited, it's parsed strictly, and a
  YAML form, if accepted, is loaded with a safe loader.
- A request that rolls an ungeneratable combination fails some fraction of the time
  rather than at submission. Checking every combination at submission would remove that,
  at the cost of an exponential check; it can be added if failures turn up in practice.
- A request's sets aren't named here. The word for them stays open, but "preset" means
  the site's named choices.

## Sources

- `docs/manifest.md`, `golf/randomizer/manifest.py`, `golf/randomizer/generate.py`,
  `golf/randomizer/music.py`, `docs/randomizer.md` (filters between the catalog and the
  pool)
- Session with jdharms, 2026-10-05 (session 019pV1kQ): simulations of expert-hole caps
  and per-nine over-par ceilings, the plans for the wind update, ALTTP mystery seeds as
  the comparison, and jdharms's choices of weighted sets of weighted fields, the PRNG
  seed as an argument, and the reserved `sets` key telling the two request shapes apart.
