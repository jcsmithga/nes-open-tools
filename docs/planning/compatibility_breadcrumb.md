I want to reduce how much compatibility and versioning machinery this project carries.
This session is for gathering context and helping me think it through. Don't change
any code or docs until we've agreed on a direction.

Background: the randomizer was designed around a seed being a recipe (a manifest of
hole ids resolved through a frozen catalog) that must stay rebuildable forever, so a
seed downloaded months later matches what competitors got. Later, the site started
storing each seed's unfinished IPS and finishing it per download, and it never rebuilds
a seed. I now believe the stored IPS provides that fairness guarantee by itself, and
that many guarantees upstream of it exist only to keep rebuilding safe, which nothing
does any more. I want to check that belief and then decide what to loosen.

Please investigate and report back:

1. Every compatibility or permanence guarantee the project makes. For each: where it is
   stated (docs, ADRs, tests in tests/meta/), what code enforces it, and what it costs
   when adding a feature. Start from docs/manifest.md, docs/catalog.md,
   docs/derived_holes.md, docs/patch_stack.md and docs/adr/ (especially 0009, 0015,
   0016, 0021, 0022, 0023).
2. Every place the site or tools read an existing seed after it is created: what they
   read (manifest, catalog, hole store, stored IPS, stored holes) and which guarantee
   each read depends on. I need to know what would break for old seeds if a guarantee
   went away.
3. Sort the guarantees into three groups: required because of ROMs already downloaded
   (QR protocol, finish ABI, SRAM), required for statistics and history to join across
   seeds, and required only so a seed could be rebuilt.

Then give me your recommendation on what to loosen, in what order, and what each step
would need (for example, storing every seed's holes as built, a tolerant manifest
reader, dropping regeneration reproducibility). Say what you verified and what you
are inferring. I'd like to end with a draft ADR stating that a seed's identity is its
stored artifact, but only after we've talked the findings through.