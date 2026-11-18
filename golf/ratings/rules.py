"""
The rules a daily is scored by, copied onto the day when it opens so a later change to the
numbers applies only to days opened after it (docs/competitive/data_model.md, Dailies):
the allowance, how Stableford counts, the formats and their small-field and tie rules, and
the index rules a scan on the day applies.
"""

from dataclasses import asdict, dataclass

from .params import IndexRules, Parameters, _index_rules

STANDING = "standing"
MATCHES = "matches"
SKINS = "skins"


@dataclass(frozen=True)
class DailyRules:
    allowance: float
    index: IndexRules
    #: Stableford points for a net par; one more for each stroke under, one fewer for
    #: each over, never below 0
    stableford_par: int = 2
    formats: tuple[str, ...] = (STANDING, MATCHES, SKINS)
    #: the fewest players a day can be won in, or its skins won
    min_field: int = 2
    #: a tied hole's skin carries to the next; skins still carried after the last hole
    #: are won by nobody
    skins_carryover: bool = True
    #: countback breaks a tie in the standing by the points over each of these last
    #: stretches of holes, in turn
    countback: tuple[int, ...] = (9, 6, 3, 1)

    @classmethod
    def from_parameters(cls, parameters: Parameters) -> "DailyRules":
        return cls(allowance=parameters.handicap.allowance, index=parameters.index)

    def to_json(self) -> dict:
        data = asdict(self)
        data["formats"] = list(self.formats)
        data["countback"] = list(self.countback)
        return data

    @classmethod
    def from_json(cls, data: dict) -> "DailyRules":
        data = dict(data)
        data["index"] = _index_rules(data["index"])
        data["formats"] = tuple(data.get("formats", cls.formats))
        data["countback"] = tuple(data.get("countback", cls.countback))
        return cls(**data)
