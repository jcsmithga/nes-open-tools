"""Manifest JSON as the schemas before the current one stored it, for tests of the legacy loaders."""

import copy

#: settings fields schema 3 added, beside the `draw_rule` schemas 3 and 4 hold
SCHEMA_3_SETTINGS = ("wind_speed_profile", "wind_direction_profile")
#: hole fields schema 3 added
SCHEMA_3_HOLE = ("wind_direction", "wind_speed")
#: settings fields schema 4 added
SCHEMA_4_SETTINGS = ("include",)
STANDARD = {"rule": "standard", "par": 72}


def draw_rule(settings: dict) -> dict:
    """The `draw_rule` schemas 3 and 4 hold for a current manifest's settings."""
    rules = settings["constraints"]
    if rules is None:
        return {"rule": "expert_cap", "per_nine": 1}
    standard = STANDARD | {"par": settings["par"]}
    if rules == [standard]:
        return {"rule": "uniform"}
    (first, cap) = rules
    assert first == standard and cap["rule"] == "expert_cap", rules
    return {"rule": "expert_cap", "per_nine": cap["per_nine"]}


def as_schema(data: dict, schema: int) -> dict:
    """A current manifest's JSON reshaped as schema 1 to 4: the fields it lacks removed.

    The manifest must have been generated as that schema's seeds were, or the result will
    not load: as a standard course with or without an expert cap for any of them, with no
    `include` for schema 1 to 3, and with no expert cap and vanilla wind for schema 1 or 2.
    Schemas 3 and 4 say which cap in a `draw_rule`, in the place `constraints` has.
    """
    data = copy.deepcopy(data)
    data["schema"] = schema
    settings = data["settings"]
    rule = draw_rule(settings)
    data["settings"] = {
        ("draw_rule" if key == "constraints" else key): (
            rule if key == "constraints" else value
        )
        for key, value in settings.items()
    }
    if schema == 4:
        return data
    for key in SCHEMA_4_SETTINGS:
        del data["settings"][key]
    if schema == 3:
        return data
    if schema == 1:
        del data["build_version"], data["finish_abi_version"]
    assert rule == {"rule": "uniform"}, rule
    for key in ("draw_rule", *SCHEMA_3_SETTINGS):
        del data["settings"][key]
    for hole in data["course"]["holes"]:
        for key in SCHEMA_3_HOLE:
            del hole[key]
    return data
