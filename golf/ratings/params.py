"""
The numbers the ratings run on, in one checked-in file (`data/parameters.json`):

- `ModelParams`: the hole model's own numbers, estimated offline by `golf-ratings
  estimate` (`golf.ratings.estimate`): the luck on a hole, how much each random-effect
  group really varies, gamma, and whether the mirrored term is fitted.
- `IndexRules`: how a handicap index is made from a player's rounds.
- `HandicapRules`: how a daily handicap is made from an index.

The rules are set by the backtest (`golf-ratings backtest`), starting from the World
Handicap System's values (docs/competitive/design.md, Setting the numbers).
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "parameters.json"

#: the random-effect groups every fit has, in column order
BASE_GROUPS = ("skill", "layout", "terrain", "pin")
#: an optional group: a player's skill in one calendar month, pulled toward their skill
MONTH = "month"


@dataclass(frozen=True)
class ModelParams:
    #: luck on one hole: the residual variance, in strokes squared
    sigma2: float
    #: how much each random-effect group really varies, in strokes squared per hole
    tau2: dict[str, float]
    #: how much a hole one stroke harder widens the gap between players
    gamma: float
    #: whether the fit has a site-wide term for a mirror-only hole
    mirror_term: bool = True

    @property
    def groups(self) -> tuple[str, ...]:
        return BASE_GROUPS + ((MONTH,) if MONTH in self.tau2 else ())

    def penalty(self, group: str) -> float:
        """The ridge penalty on a group's levels: sigma^2 / tau^2."""
        return self.sigma2 / max(self.tau2[group], 1e-12)


@dataclass(frozen=True)
class IndexRules:
    #: rounds in the index: the best `window` of the last `best_of`, or with `best_of`
    #: None the last `window`
    window: int = 8
    best_of: int | None = 10
    #: every player starts from this index, worth `start_weight` rounds, fading to
    #: nothing as their own rounds fill the window
    start_index: float = 0.0
    start_weight: float = 4.0
    #: a hole counts no worse than par + this + the strokes received there
    hole_cap_over_par: int = 2
    #: exceptional score reduction: a differential this many strokes better than the
    #: index the player held takes `exceptional_cut` strokes off every differential in
    #: the record, so the index drops at once and the cut fades as they age out. The
    #: World Handicap System's: 7.0 better takes 1.0, 10.0 better takes 2.0. None is off
    exceptional: tuple[float, float] | None = (7.0, 10.0)
    exceptional_cut: tuple[float, float] = (1.0, 2.0)
    #: past `soft_cap` over the low mark a rise counts half; never past `hard_cap`.
    #: None turns a cap off
    soft_cap: float | None = 3.0
    hard_cap: float | None = 5.0
    #: the low mark is the lowest index of this many days, once a player has
    #: `low_min_rounds` counted rounds
    low_window_days: int = 365
    low_min_rounds: int = 20


@dataclass(frozen=True)
class HandicapRules:
    #: the share of a course handicap a player receives in a daily: a round number
    #: that gives the stronger player about `golf.ratings.backtest.TARGET` of their
    #: net matches, changed only when the target changes or the share drifts from it
    #: (docs/competitive/design.md, The allowance)
    allowance: float = 0.9


def _index_rules(data: dict) -> IndexRules:
    data = dict(data)
    for key in ("exceptional", "exceptional_cut"):
        if data.get(key) is not None:
            data[key] = tuple(data[key])
    return IndexRules(**data)


@dataclass(frozen=True)
class Parameters:
    model: ModelParams
    index: IndexRules = field(default_factory=IndexRules)
    handicap: HandicapRules = field(default_factory=HandicapRules)
    #: where the model's numbers came from: the database, its rounds, the date
    provenance: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "model": asdict(self.model),
            "index": asdict(self.index),
            "handicap": asdict(self.handicap),
            "provenance": self.provenance,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Parameters":
        return cls(
            model=ModelParams(**data["model"]),
            index=_index_rules(data.get("index", {})),
            handicap=HandicapRules(**data.get("handicap", {})),
            provenance=data.get("provenance", {}),
        )

    def text(self) -> str:
        return json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n"

    def digest(self) -> str:
        """The hash a ratings run records."""
        return hashlib.sha256(self.text().encode()).hexdigest()

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Parameters":
        return cls.from_json(json.loads(path.read_text()))

    def save(self, path: Path = DEFAULT_PATH) -> None:
        path.write_text(self.text())
