# Spikes

Experimental scripts for ideas still being explored. They have no entry points, no
tests, and no promise of staying around or working: a spike either grows into a real
tool (moved out of here, with an entry point and tests) or is deleted. Run one with
`uv run python -m tools.spikes.<name>`. A spike's write-up, if it has one, sits beside
it as a Markdown file of the same name.

| Script | What it explores |
|--------|------------------|
| `player_skill.py` | Rating randomizer players from every hole score in a site database (`golf_site.db`), adjusting for the layout, terrain, pin and wind each player drew. Written up in `player_skill.md`. The model now lives in `golf/ratings/` (`golf-ratings`); the spike stays as the record behind its write-up |
| `handicap_backtest.py` | Backtesting handicapped formats on a site database: handicap schemes and allowances scored over every pair of players on every shared seed, how fast a rating settles, and simulated seasons. Written up in `docs/competitive/design.md`. Its maintained successor is `golf-ratings backtest`; the spike stays as the record behind the design's Evidence section |
| `bank_split.py` | Which contiguous parts of a switchable bank could move to another bank: every reference crossing a region's boundary, from the code trace. Written up in `docs/bank13_relocation.md` |
