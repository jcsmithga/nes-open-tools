"""
Ratings for the randomizer's competitive play: the hole model fitted to every hole score
the site holds (`model`), its parameters and rules (`params`), the reference holes it is
anchored to (`reference`), a seed's course rating, slope and stroke indexes (`seed`), a
player's handicap index (`index`), a daily handicap and its strokes (`handicap`), the
net formats (`formats`), the offline estimate of the model's parameters (`estimate`),
the backtest (`backtest`) and reading a site database (`sitedb`).

The design is docs/competitive/design.md, Handicaps, and the decision ADR 0034. Only numpy: the daily refit of the
hole ratings and the index update at each scan run on the web server.
"""
