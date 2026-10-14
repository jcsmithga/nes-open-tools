"""
Requests: a settings file a player keeps and rolls again and again.

This is the plain half of ADR 0016's request, the settings request with no weights: a
partial `Settings` object applied over the defaults. A key left out takes its default, at
every level, so `{}` is a default seed and `{"clubs": {"max": 10}}` a bag of at most 10
clubs with nothing banned. An unknown key is an error.

Not here: weighted fields (`one_of`) and weighted sets (`sets`), which ADR 0016 adds, and
which this refuses by name. `prng_seed` is refused too: a seed belongs to one roll, and
is an argument.

See docs/manifest.md.
"""

import json
from pathlib import Path

from .manifest import ManifestError, Settings

#: keys ADR 0016's weighted requests use, refused here with a pointer to it
_WEIGHTED = ("sets", "one_of")


class RequestError(ManifestError):
    """A request that is not a settings object, or names something no settings have."""


def _refuse_weights(data: object, where: str) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in _WEIGHTED:
                raise RequestError(
                    f"{where}: {key!r} is a weighted request (ADR 0016), which this "
                    "code does not resolve yet"
                )
            _refuse_weights(value, f"{where}.{key}")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _refuse_weights(item, f"{where}[{index}]")


def settings_from_request(data: object, prng_seed: str | None = None) -> Settings:
    """The settings a request asks for, with `prng_seed` as the seed to roll them with."""
    if not isinstance(data, dict):
        raise RequestError(f"a request is an object of settings, got {data!r}")
    _refuse_weights(data, "request")
    if "prng_seed" in data:
        raise RequestError(
            "a request holds no prng_seed: a seed belongs to one roll; pass it with --seed"
        )
    defaults = Settings().to_json()
    asked = data
    unknown = sorted(set(asked) - set(defaults))
    if unknown:
        raise RequestError(
            f"unknown settings {unknown}; a request may set "
            f"{sorted(set(defaults) - {'prng_seed'})}"
        )
    merged = dict(defaults)
    for key, value in asked.items():
        # only the club rules are an object with defaults of their own
        if key == "clubs" and isinstance(value, dict):
            merged[key] = defaults[key] | value
        else:
            merged[key] = value
    merged["prng_seed"] = prng_seed
    return Settings.from_json(merged)


def load_request(path: Path, prng_seed: str | None = None) -> Settings:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as problem:
        raise RequestError(f"{path}: not JSON: {problem}") from None
    return settings_from_request(data, prng_seed)
