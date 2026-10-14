# Test suite performance audit

Measured on this machine on 2026-10-04 with `uv run pytest -n 24`, normal
coverage enabled, both ROMs and rehydrated courses present, and Chromium
available. Physics tests remain excluded through the existing default option;
physics-model checks already in the main suite remain included.

## Measurements

| Configuration | Tests passed | Runtime |
|---|---:|---:|
| Baseline, default load scheduler | 3,202 | 83.66 s |
| First workload split | 3,634 | 71.32 s |
| Further green/import splits | 3,932 | 73.44 s |
| Browser probes moved out of collection | 3,932 | 65.98 s |
| Exact hazard cutoff checks and work stealing | 3,944 | 50.12 s |
| First normal-command confirmation | 3,944 | 48.67 s |
| Further QR/layout review | 3,939 | **50.77 s** |

Counts primarily increase because loops become parametrized cases. The hazard
probability checks also now test exact cutoffs instead of statistical frequencies.
Coverage remained 78%, with the same 22,940 statements and 4,964 missing statements.
Normal pytest configuration now selects `--dist=worksteal`, including when invoked
with `uv run pytest -n 24`. The 50.12-second run additionally loaded a temporary
phase-timing plugin: worker collection took 2.14–3.58 seconds.

Reproduce timings with:

```sh
uv run pytest -n 24 --durations=40 --junitxml=/tmp/test-durations.xml
```

Durations below are individual setup/call times under concurrent load, not
isolated benchmarks or quantities to add to get wall time. Module/session
fixtures are shared within a worker, not across xdist workers.

## Slow test review

| Baseline workload | Seconds | Failure it can detect / judgment | Action |
|---|---:|---|---|
| Hazard consistency, uniform / weighted | 35.18 / 19.14 | Repainting fairway, changing terrain, painting only part of a connected hazard, or assigning a non-hazard palette. Useful semantic coverage beyond golden hashes. | Parametrize all 144 holes for each style; retain all three seeds and all assertions. |
| Green table fixture | 34.96 setup | Incorrect stop/aim/speed indexing or putt results; exercised by comparisons below. | Build one pixel and speed per case; retain all reachable stops and 25 aims at each speed. |
| Transform golden output | 26.74 | Changed PRNG consumption, hazard probabilities, mirror tile mapping, or transform dispatch changes transform output. A semantic round trip cannot replace this check. | Parametrize the five existing golden hashes; preserve every input and digest. |
| Mirror fixture and inverse check | 23.65 setup + 10.54 call | Incorrect tile partners, metadata reflection, checkerboard phase, tee or pin coordinates. Involution alone can miss a consistently wrong reflection axis; the explicit coordinate and placement checks matter. | Combine inverse and placement assertions per hole; compute the first mirror once, independently for all 144 holes. |
| Green oracle comparison, skill 3 / skill 1 | 17.08 / 2.76 | Incorrect error weights, wrapping, stop lookup, probability aggregation, or cup handling. Both skills matter: the larger error window exercises more neighboring stops/aims. | Six pixel/speed cases, each sharing its freshly built table between both skills. Same 144 oracle comparisons overall. |
| Fresh imports | 14.18 | An import-order-dependent circular import or an otherwise unused module with an invalid import. Same-process imports are not substitutes. | Parametrize every module's fresh interpreter; remove the nested eight-thread pool so xdist schedules each import. |
| Feature style table current | 10.81 | Stale style counts, changed feature classification or counting. Useful generated-data contract. | Keep; current table/fitter caching already avoids repeatedly loading the stored data. |
| Shifted boundary paint | 10.76 | A seam repair/locking bug leaves the moved boundary broken or uses tiles absent from the vanilla style. | Keep the aggregate thresholds and corpus. |
| BACK2 versus BACK1 | 9.36 | Removing screen/lie/club-specific spin suppression, or suppressing BACK2 on fairway irons too. The explicit inequality is essential. | Keep. |
| Boundary fits its original shape | 7.96 | Fitter loses tile identity or continuity. The shifted-boundary test checks a different use case. | Keep. |
| Screenshot expand / generate / ready download form | 8.61 / 7.66 / 5.20 | CLI fails to open sections, submit generation, or wait for ROM verification/download readiness. | Keep browser checks; HTTP tests cannot exercise these behaviors. |
| Rehydrate CLI / US fixture | 7.39 / 6.58 setup | Incorrect CLI wiring, missing JP output, wrong check exit codes, missing installation or rendered assets. Corrupting one installed JP hole must make check fail. | Keep. Unit preflight tests do not replace real dump/install/render coverage. |
| Browser ROM download | 6.84 | The JavaScript IPS applier writes incorrect bytes, settings fail to persist, or the download uses the wrong options. Compares the downloaded bytes with Python finishing. | Keep; this is the sole browser IPS-applier test. |
| Build with transforms | 6.46 | Builder ignores the manifest's transforms or writes the wrong transformed course. Direct transform tests do not cover builder wiring. | Keep. |
| Known-data fixture | 5.62 setup | Region discovery produces wrong bounds, overlaps traced code, or mismeasures data. | Keep. This fixture can be rebuilt on multiple workers; sharing a serialized result would add complexity for a smaller gain. |
| Imported music APU trace | 4.13 / 3.97 | Wrong envelope mapping, transpose adjustment or stream relocation changes actual engine output. Byte extraction alone cannot catch every playback error. | Keep both source comparison and untouched-track checks. |
| Rangefinder browser interactions | 3.78 | Zoom, hole selection, measurement or green navigation fails in JavaScript. | Keep. |
| Uniform/weighted hazard probabilities | 2.53 / 2.51 | Changed probability cutoff or failure to scale flips by hazard area. Frequency checks over 400 seeds are an indirect check of a deterministic policy. | Replace with exact below/at/above-cutoff checks for both kinds, small/threshold/large sizes. Seeded redraws and golden hashes retain PRNG integration coverage. |

These are designed mutations for the reviewed workloads, not a claim that every
listed mutation was executed or that the tests prove complete correctness.

## Executed fault injections

Temporary pytest monkeypatches, outside the repository, verified these failures:

- Map redrawn water to fairway palette 1: both hazard supertile tests fail.
- Shift a mirrored tee by 16 pixels: the vanilla placement check fails.
- Make uniform hazard redraw always choose water: the existing golden hash fails.
- Multiply green lookup probabilities by 0.9: the real green/oracle comparison fails.
- Make `random.shuffle` a no-op: the strengthened fringe randomness test fails.
- Ignore hazard size in the weighted policy: both large-hazard cutoff cases fail.

The fringe test previously asserted `len(results) >= 1` after 50 successful
iterations. This is guaranteed regardless of whether any randomness exists. It
now requires more than one result, using the fixture's compatible extra tile.

The hazard supertile test also used to construct a complete synthetic hole for
each of 165 attribute comparisons in each of 40 seeds. It now constructs the
original once per test and compares against it.

## Scheduling and collection

The default load scheduler sends tests in collection order. With uneven test
costs, splitting large loops alone did not achieve the target. xdist's existing
work-stealing scheduler starts workers with queues and lets idle workers take
unstarted tests from busier workers. It is now the default, with no custom timing
cache or fixed list of prioritized tests.

Four browser modules used to launch Chromium at import time to decide their skip
markers. Every worker imports every module: 24 workers meant 96 availability
probes before test execution. A session fixture now probes only workers actually
running browser tests, once per worker. Browser tests continue to launch their
own browser and preserve their original assertions. Missing Chromium still skips
them; missing ROMs are checked before running the fixture. A separate run with
`PLAYWRIGHT_BROWSERS_PATH` pointing at an empty location verified that a browser
test still skips cleanly.

## Redundancy and caching

Golden output, inverse/placement checks, and semantic hazard checks overlap in
inputs but protect different contracts. Removing any one would lose either
seed compatibility, intended behavior, or integration wiring. Repeated expensive
setup is the useful redundancy to remove: mirror assertions share one transform,
and both green skills share one fresh table per pixel/speed.

A cProfile run of the original weighted hazard consistency test (without
coverage) recorded 30.83 seconds and 92.6 million calls. `feature_groups` ran
1,008 times, of which `find_features` consumed 28.16 seconds: roughly **93%** of
the test call. Loading the 144 holes took only 0.12 seconds. Caching file reads
is therefore not the main opportunity there.

A future production optimization could separate terrain-only connected-component
geometry from palette classification. Cache geometry with a bounded cache keyed
by immutable visible terrain, then recompute kind from current attributes. A
cache keyed only by the mutable `HoleData` object's identity would return stale
results after painting; cached features would also need immutable supertile
counts. This optimization was left out of this test-only change.

Persistent caches of transformed holes, generated style counts or simulated green
results would need implementation-sensitive invalidation. Reusing those results
across runs could conceal precisely the regressions these tests should catch.
Fresh computation distributed across workers avoids that risk.

The subsequent review consolidated the QR stage checks per round, avoiding
repeated emulation without caching subject output; details follow.


## Further review: the next tier

The next pass considered aggregate worker time as well as individual slow calls.
After the first changes, flights used 72.70 worker seconds, fresh imports 67.13,
music import 42.09, QR capture 11.58, QR port 7.65, and layouts 6.86. These are
sums under concurrent load, not independent wall-time estimates.

Two further changes were justified:

- **QR port:** six tests each replayed the same six rounds up to different
  stages. Now six round cases each run one fresh pipeline and check payload/MAC,
  URL, data codewords, error correction, interleave, matrix, nametable and real
  decoding as they go. Checking the earlier buffers before nametable construction
  matters because they share RAM. The entry-point equivalence test still runs a
  separate complete entry-point call; slot selection and clamping tests remain.
- **Layouts:** count, exhaustive validation, sorting and uniqueness used separate
  cases, causing repeated layout construction on different workers. Each par now
  has one case covering every member. Strict ordering also proves uniqueness.
  The exhaustive assertions directly count pars, compare nines, and inspect
  adjacent pairs instead of using the generator's predicate helpers. Explicit
  valid, wrong-count and unbalanced examples exercise those helpers separately.

Single-process diagnostic runs, with coverage disabled, measured:

| Module | Before | After |
|---|---:|---:|
| QR port | 3.11 s | 1.19 s |
| Layouts | 1.94 s | 1.08 s |

The full covered suite passed 3,939 tests in 50.77 seconds. Coverage remained
78%, with 22,940 statements and 4,964 missing statements. The prior full run
was 48.67 seconds, so this pass establishes less repeated work and better
assertions, **not a demonstrated improvement in full-suite wall time**. Five
fewer collected cases come from consolidating nine layout cases into three and
adding one focused predicate case; no layout or QR round inputs were discarded.

Additional temporary mutations were executed:

| Mutation | Observed failure |
|---|---|
| Flip the last QR data pad byte after codeword construction | Per-round data-codeword assertion fails before error correction or decoding. |
| Skip `QrBuildCode` while leaving individual stages intact | Separate entry-point equivalence assertion fails. |
| Make layout count validation always return true | New wrong-count example fails. The entire previous layout module passed this mutation. |
| Make layout balance validation always return true | New unbalanced example fails. |
| Shift a reused flight's X coordinate by one fractional unit | US hole 1 differential test fails on full result equality. |

The remaining reviewed tests earn their cost:

| Workload | Why it remains useful / efficiency judgment |
|---|---|
| Mirror golden hash | Inverse tests intentionally exempt regenerated forest tiles. The golden hash catches a change to those forest choices too. Removing it would lose that protection. |
| Flight differential tests | Every US hole, multiple launch lies, moved starts, RNG states and pins exercise a terrain-sensitive accelerator against direct simulation. Dedicated cases require air, landing, roll and whole-flight reuse and cover historical green-view regressions. These test accelerator equivalence, not independent correctness of shared physics primitives. Scanning the terrain to select starts is expensive but preserves the deterministic corpus. |
| Flight cache counters | Merely returning direct simulation would pass numerical comparisons; `flown`/`shared` counters also require actual reuse. Keep both kinds of check. |
| Imported and untouched music traces | Extracted bytes cannot prove the engine plays an envelope or transpose correctly. Imported traces compare to JP; untouched traces compare to US. Whole-theme and single-theme paths allocate and redirect differently. Shortening traces could omit a later pattern or loop. |
| Known-region and scene-object traces | Region bounds, code/data overlap, unresolved control flow and bank selection are different assertions over the same trace. Fixtures can be rebuilt across workers, but a shared serialized cache would need careful invalidation; retained for now. |
| QR capture conditions | Exact matrix equality does not test legibility after aspect correction, blur, rotation, JPEG or scanlines. Both decoder implementations and varied payloads matter; clean decoder tests are not substitutes. |
| QR display tests | The port's RAM matrix does not prove correct PPU upload addresses, blank background, quiet zone, captions or dismissal behavior. These inspect actual simulated VRAM and controller handling. |
| QR encoder reference and capacity tests | All eight masks and header/capacity boundaries check independent spec agreement; fixed-mask capture checks only the production mask and URL length. |
| Fresh-import tests | A successful normal import cannot exclude a circular import that only fails from another starting module. Their subprocess overhead is inherent to this check; keep every starting module. |
| Generated style and hazard-stamp freshness | Check the entire source corpus against committed artifacts. Reducing the corpus would allow stale data outside the sample to pass. Result caching would conceal regeneration bugs. |
| Build with transforms | Protects manifest-to-builder wiring, including transform composition, rather than only transform algorithms. Its expected transformed holes share algorithms with the builder, so golden and semantic transform tests remain necessary. |
| Browser screenshots, ROM setup and rangefinder | Test CLI orchestration, real JavaScript ROM verification, persisted settings and navigation. Server-side response checks do not exercise these paths. |

Further reductions would mostly require optimizing production geometry, tracing
or simulation, or narrowing the checked corpus. Neither is justified solely by
this review while the complete suite is around 50 seconds.
