"""
Requests: what a seed is asked for, which `resolve` turns into concrete settings (ADR 0016).

A request has one of two shapes, told apart by the reserved `sets` key:

- **A settings request**: a partial `Settings` object applied over the defaults. A key
  left out takes its default, at every level, so `{}` is a default seed and
  `{"clubs": {"max": 10}}` a bag of at most 10 clubs with nothing banned. Any field may
  instead be a weighted choice, `{"one_of": [{"weight": w, "value": v}, ...]}`, each value
  written as the field would be.
- **A sets request**: `{"sets": [{"weight": w, "settings": <settings request>}, ...]}`,
  a weighted choice of settings requests, and nothing beside `sets`.

Weights are non-negative integers, relative to one another, and every choice needs one
above zero. `resolve` picks the set, then each weighted field in `Settings` field order,
from the seed's own stream (`stream(prng_seed, "request")`), so within one release the
same request and seed resolve the same way. The PRNG seed is an argument, never part of
the request, and the mercy point is not a request's to set: it takes its default.

`check_request` is what a request is judged by when it is submitted: every set, and
every value of every weighted field, must make legal settings, so a request that resolves
at all resolves to settings that load. An unknown key is an error, and a near miss of
`sets` asks whether it was meant.

See docs/manifest.md.
"""

import json
import random
from collections.abc import Iterator, Mapping
from pathlib import Path

from .generate import new_prng_seed, stream
from .manifest import ManifestError, Settings

SETS = "sets"
ONE_OF = "one_of"
#: keys a request reserves at the top level, which no settings field may take
RESERVED = (SETS,)
#: settings a request may not set, which always take their defaults: players don't
#: choose the mercy point for now
HELD = ("mercy_point",)
#: what a near miss of a reserved key looks like, lowercased
_NEAR_SETS = ("set", "sets", "settings_sets")


class RequestError(ManifestError):
    """A request that is not one, or that asks for settings that cannot be."""


def _fields() -> list[str]:
    """The settings fields a request may set, in `Settings` field order."""
    return [
        key for key in Settings().to_json() if key != "prng_seed" and key not in HELD
    ]


def _weights(options: object, what: str, value_key: str) -> list[tuple[int, object]]:
    """A list of `{"weight": w, value_key: v}`, as (weight, v) pairs."""
    if not isinstance(options, list) or not options:
        raise RequestError(f"{what}: expected a non-empty list of choices")
    choices = []
    for index, option in enumerate(options):
        where = f"{what}[{index}]"
        if not isinstance(option, dict) or set(option) != {"weight", value_key}:
            raise RequestError(
                f'{where}: expected {{"weight": w, "{value_key}": ...}}, got {option!r}'
            )
        weight = option["weight"]
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise RequestError(f"{where}: weight must be a whole number 0 or more")
        choices.append((weight, option[value_key]))
    if not any(weight for weight, _ in choices):
        raise RequestError(f"{what}: every weight is 0; give one choice a weight")
    return choices


def _choose(choices: list[tuple[int, object]], rng: random.Random) -> object:
    """A choice drawn by its weight, exactly: integers all the way."""
    pick = rng.randrange(sum(weight for weight, _ in choices))
    for weight, value in choices:
        if pick < weight:
            return value
        pick -= weight
    raise AssertionError("a draw below the total lands in some choice")


def _sets(data: object) -> list[tuple[int, Mapping]]:
    """A request's sets, each a weight and a settings request; a settings request is
    one set."""
    if not isinstance(data, dict):
        raise RequestError(f"a request is an object of settings, got {data!r}")
    if SETS not in data:
        return [(1, data)]
    if set(data) != {SETS}:
        raise RequestError(
            f"a sets request holds only {SETS!r}; put {sorted(set(data) - {SETS})} "
            "in each set's settings"
        )
    sets = _weights(data[SETS], SETS, "settings")
    for index, (_, settings) in enumerate(sets):
        if not isinstance(settings, dict):
            raise RequestError(f"{SETS}[{index}] settings: expected an object")
    return [(weight, settings) for weight, settings in sets]  # type: ignore[misc]


def _check_keys(request: Mapping, what: str) -> None:
    fields = _fields()
    for key in request:
        if key == "prng_seed":
            raise RequestError(
                f"{what}: a request holds no prng_seed: a seed belongs to one roll; "
                "pass it with --seed"
            )
        if key in fields:
            continue
        if key in HELD:
            raise RequestError(
                f"{what}: a request can't set {key}; seeds take its default"
            )
        if key.lower() in _NEAR_SETS:
            raise RequestError(f"{what}: unknown key {key!r}; did you mean {SETS!r}?")
        raise RequestError(
            f"{what}: unknown settings [{key!r}]; a request may set {sorted(fields)}"
        )


def _weighted(value: object) -> bool:
    return isinstance(value, dict) and ONE_OF in value


def _check_nested(value: object, what: str) -> None:
    """Refuse a weighted choice or `sets` anywhere but a whole top-level field."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in (ONE_OF, SETS):
                raise RequestError(
                    f"{what}: {key!r} chooses a whole field of a request, not part of one"
                )
            _check_nested(inner, f"{what}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            _check_nested(inner, f"{what}[{index}]")


def _options(request: Mapping, what: str) -> dict[str, list[tuple[int, object]]]:
    """Each weighted field's choices, after checking every field's shape."""
    _check_keys(request, what)
    options = {}
    for key, value in request.items():
        if _weighted(value):
            if set(value) != {ONE_OF}:  # type: ignore[arg-type]
                raise RequestError(
                    f"{what}.{key}: a weighted field holds only {ONE_OF!r}"
                )
            choices = _weights(value[ONE_OF], f"{what}.{key}.{ONE_OF}", "value")  # type: ignore[index]
            for _, choice in choices:
                _check_nested(choice, f"{what}.{key}")
            options[key] = choices
        else:
            _check_nested(value, f"{what}.{key}")
    return options


def _settings(fixed: Mapping, prng_seed: str | None) -> Settings:
    """Settings from a request whose every field has one value."""
    merged = Settings().to_json()
    for key, value in fixed.items():
        # only the club rules are an object with defaults of their own
        if key == "clubs" and isinstance(value, dict):
            merged[key] = merged[key] | value
        else:
            merged[key] = value
    merged["prng_seed"] = prng_seed
    return Settings.from_json(merged)


def _each_value(
    request: Mapping, options: dict[str, list[tuple[int, object]]]
) -> Iterator[tuple[str, dict]]:
    """The request with each value of each weighted field in turn, every other weighted
    field at its first choice with a weight, and what the value is."""
    first = {
        key: next(value for weight, value in choices if weight)
        for key, choices in options.items()
    }
    base = {**request, **first}
    yield "", base
    for key, choices in options.items():
        for index, (_, value) in enumerate(choices):
            yield f"{key}.{ONE_OF}[{index}]", {**base, key: value}


def check_request(data: object) -> None:
    """Refuse a request any of whose sets, or any value of whose weighted fields, does
    not make legal settings."""
    for index, (_, request) in enumerate(_sets(data)):
        what = "request" if SETS not in data else f"{SETS}[{index}] settings"  # type: ignore[operator]
        options = _options(request, what)
        for where, fixed in _each_value(request, options):
            try:
                _settings(fixed, None)
            except RequestError:
                raise
            except ManifestError as problem:
                at = f"{what}.{where}" if where else what
                raise RequestError(f"{at}: {problem}") from None


def resolve(data: object, prng_seed: str | None = None) -> Settings:
    """The concrete settings a request resolves to, rolled from `prng_seed` (drawn when
    None), which the settings record."""
    check_request(data)
    if prng_seed is None:
        prng_seed = new_prng_seed()
    rng = stream(prng_seed, "request")
    sets = _sets(data)
    request: Mapping = (
        sets[0][1] if len(sets) == 1 else _choose(list(sets), rng)  # type: ignore[assignment]
    )
    options = _options(request, "request")
    fixed: dict[str, object] = dict(request)
    for key in _fields():
        if key in options:
            fixed[key] = _choose(options[key], rng)
    return _settings(fixed, prng_seed)


def is_weighted(data: object) -> bool:
    """Whether a checked request leaves anything to be rolled: sets, or a weighted
    field."""
    return any(
        len(sets) > 1 or any(_weighted(value) for value in request.values())
        for sets in [_sets(data)]
        for _, request in sets
    )


def load_request(path: Path) -> dict:
    """A request file's JSON, checked."""
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as problem:
        raise RequestError(f"{path}: not JSON: {problem}") from None
    check_request(data)
    return data
