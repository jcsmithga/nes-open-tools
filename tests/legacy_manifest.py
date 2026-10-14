"""Manifest JSON as the schemas before the current one stored it, for tests of reading
stored seeds (`StoredManifest`)."""

import copy

#: settings fields schema 3 added
SCHEMA_3_SETTINGS = ("draw_rule", "wind_speed_profile", "wind_direction_profile")
#: hole fields schema 3 added
SCHEMA_3_HOLE = ("wind_direction", "wind_speed")
#: settings fields schema 4 added
SCHEMA_4_SETTINGS = ("include",)


def as_schema(data: dict, schema: int) -> dict:
    """A current manifest's JSON reshaped as schema 1, 2 or 3: the fields it lacks removed.

    The manifest should have been generated as that schema's seeds were, or what a stored
    reader supplies for the missing fields is not what it was made with: no `include` for
    any of them, and a uniform draw and vanilla wind for schema 1 or 2.
    """
    data = copy.deepcopy(data)
    data["schema"] = schema
    for key in SCHEMA_4_SETTINGS:
        del data["settings"][key]
    if schema == 3:
        return data
    if schema == 1:
        del data["build_version"], data["finish_abi_version"]
    for key in SCHEMA_3_SETTINGS:
        del data["settings"][key]
    for hole in data["course"]["holes"]:
        for key in SCHEMA_3_HOLE:
            del hole[key]
    return data
