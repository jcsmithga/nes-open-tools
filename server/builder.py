"""What the site's routes call to build: a manifest from settings, its unfinished IPS, and a finished IPS.

The builder holds the catalog, curation and hole store the site generates from, and reads
the server's vanilla US ROM on first use. `warm` builds ahead of time what the first seed
would otherwise wait for, which the app does at startup. Builds run behind a semaphore, so a burst of
requests queues instead of building side by side. `create_app` takes a builder so tests
can stand one in that never reads a ROM.
"""

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from golf.core.patches import QrCredentials
from golf.formats.hole_data import HoleData
from golf.randomizer.build import (
    PlayerOptions,
    build_unfinished,
    finish,
    signpost_step,
)
from golf.randomizer.catalog import DEFAULT_INDEX, US_ROM, Catalog, HoleStore
from golf.randomizer.curation import DEFAULT_CURATION, CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Manifest, Settings, StoredManifest
from golf.randomizer.roms import vanilla_rom

from .config import Config
from .timings import Sample, phase


class BuilderUnavailableError(Exception):
    """The server cannot build seeds: its vanilla ROM is missing or not the vanilla ROM."""


@dataclass(frozen=True)
class BuiltSeed:
    """What a build leaves for the seed's row."""

    unfinished_ips: bytes
    #: the 18 holes as built, transforms applied
    holes: tuple[HoleData, ...]


class SeedBuilder:
    def __init__(
        self,
        catalog: Catalog,
        curation: CurationSnapshot,
        store: HoleStore,
        rom_path: Path,
        concurrency: int = 1,
    ):
        self.catalog = catalog
        self.curation = curation
        self.store = store
        self.rom_path = Path(rom_path)
        self._builds = threading.Semaphore(concurrency)
        self._rom_lock = threading.Lock()
        self._vanilla: bytes | None = None

    @classmethod
    def from_config(cls, config: Config) -> "SeedBuilder":
        return cls(
            catalog=Catalog.load(DEFAULT_INDEX),
            curation=CurationSnapshot.load(DEFAULT_CURATION),
            store=HoleStore(config.holes_dir),
            rom_path=Path(config.rom_dir) / vanilla_rom(US_ROM).filename,
        )

    def vanilla(self) -> bytes:
        """The server's vanilla US ROM, read and checked once."""
        with self._rom_lock:
            if self._vanilla is None:
                try:
                    data = self.rom_path.read_bytes()
                except OSError as problem:
                    raise BuilderUnavailableError(
                        f"cannot read the vanilla ROM at {self.rom_path}: {problem}"
                    ) from None
                expected = vanilla_rom(US_ROM).sha1
                if hashlib.sha1(data).hexdigest() != expected:
                    raise BuilderUnavailableError(
                        f"{self.rom_path} is not the vanilla US ROM (SHA-1 {expected})"
                    )
                self._vanilla = data
            return self._vanilla

    def warm(self) -> None:
        """Build the signpost banner, cached for later seeds.

        Raises BuilderUnavailableError when the ROM is missing.
        """
        signpost_step(self.vanilla())

    def generate(self, settings: Settings, request: dict | None = None) -> Manifest:
        """A seed's manifest; `request` is what the settings were resolved from."""
        return generate(self.catalog, self.curation, settings, request)

    def build(self, manifest: Manifest, sample: Sample | None = None) -> BuiltSeed:
        """The seed's unfinished IPS and built holes, what `insert_seed` stores.

        With a sample, the wait for the build semaphore is timed apart from the build
        itself: a burst of requests queues here, and it is the queue that grows.
        """
        vanilla = self.vanilla()
        with phase(sample, "queue"):
            self._builds.acquire()
        try:
            with phase(sample, "build"):
                built = build_unfinished(manifest, self.catalog, self.store, vanilla)
                return BuiltSeed(built.ips, built.holes)
        finally:
            self._builds.release()

    def finish(
        self,
        manifest: Manifest | StoredManifest,
        unfinished_ips: bytes,
        options: PlayerOptions,
        credentials: QrCredentials | None = None,
    ) -> bytes:
        """A player's finished IPS for a stored seed: signed in with credentials, a guest without.

        Finishing takes milliseconds, so it skips the semaphore.
        """
        return finish(
            manifest, self.vanilla(), unfinished_ips, options, credentials
        ).ips
