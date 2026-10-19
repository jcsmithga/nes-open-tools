"""Integration tests: the cutscene golfer sprites against the real vanilla ROM.

They pin the identification in `golf/core/cutscene_sprites.py`: each scene's
frames come from its own animation streams, its CHR covers every tile those
frames draw, and Mario wears the shot screen's colors.
"""

from pathlib import Path

import pytest

from golf.core import object_script
from golf.core.cutscene_sprites import (
    SCENE_BANK,
    SETS,
    SPRITE_BANK,
    CutsceneSprites,
    parse_metasprite,
    set_named,
)
from golf.core.graphics_codec import load_graphics_table
from golf.core.rom_reader import RomReader

ROM_PATH = "nes_open_us.nes"
LOAD_COMPRESSED_GRAPHICS = bytes([0x20, 0x5F, 0xD4])

pytestmark = pytest.mark.skipif(
    not Path(ROM_PATH).exists(), reason=f"{ROM_PATH} not present"
)


@pytest.fixture(scope="module")
def rom():
    return RomReader(ROM_PATH)


@pytest.fixture(scope="module")
def sprites(rom):
    return CutsceneSprites(rom)


def scenes():
    return [(s, scene) for s in SETS for scene in s.scenes]


def scene_id(value):
    return f"{value[0].name}-{value[1].name}"


class TestMetaspriteFormat:
    def test_both_chunk_forms_and_the_chain_bit(self):
        data = bytes([0x41, 0xF8, 0x10, 0x40, 0x08, 0x82, 0x01, 0x00, 0x11, 0xF8])
        data += bytes([0x08, 0x12, 0x00])
        meta = parse_metasprite(data, 0x9000)
        assert meta.byte_length == len(data)
        assert [(s.dy, s.tile, s.attr, s.dx) for s in meta.sprites] == [
            (-8, 0x10, 0x40, 8),
            (0, 0x11, 0x01, -8),
            (8, 0x12, 0x01, 0),
        ]

    def test_empty(self):
        assert len(parse_metasprite(bytes([0x00, 0xFF]))) == 0


class TestScenes:
    @pytest.mark.parametrize("case", scenes(), ids=scene_id)
    def test_setup_loads_the_first_chr_table(self, rom, case):
        _, scene = case
        bank, addr = scene.chr_tables[0]
        code = rom.read_switched(0x8000, SCENE_BANK, 0x4000)
        wanted = LOAD_COMPRESSED_GRAPHICS + bytes([bank, addr & 0xFF, addr >> 8])
        # The hole result picks its load by player, a few instructions apart.
        near = code[scene.setup - 0x8000 : scene.setup - 0x8000 + 0x60]
        assert wanted in near

    @pytest.mark.parametrize("case", scenes(), ids=scene_id)
    def test_chr_covers_every_tile_drawn(self, rom, sprites, case):
        sprite_set, scene = case
        written = sprites.load_chr(scene).touched
        for frame, meta in sprites.frames(sprite_set, scene):
            for sprite in meta.sprites:
                assert sprite.tile * 16 in written, f"frame ${frame:02X}"

    def test_frames_are_the_ones_the_streams_show(self, rom):
        result = object_script.trace_everything(rom)[0]
        walk = object_script.walk_objects(rom, result)
        shown: dict[int, set[int]] = {}
        for index, record in enumerate(walk.records):
            if walk.banks.get(index) != SPRITE_BANK:
                continue
            stream = object_script.walk_stream(
                rom, SPRITE_BANK, record.anim, True, record.sprite_id
            )
            for sprite_id, frame in stream.uses:
                shown.setdefault(sprite_id, set()).add(frame & 0x7F)
        for sprite_set in SETS:
            assert set(sprite_set.frames) <= shown[sprite_set.sprite_id], (
                sprite_set.name
            )
        # Sprites $02 and $03 hold nothing but the golfer; $1A is the one spare.
        assert shown[0x02] == set(set_named("hole_result").frames)
        assert shown[0x03] == set(set_named("hole_result_2p").frames)


class TestMario:
    @pytest.mark.parametrize("case", scenes(), ids=scene_id)
    def test_sprite_palette_0_is_the_shot_screen_mario(self, sprites, case):
        _, scene = case
        assert sprites.sprite_palettes(scene)[0][1:] == [0x25, 0x0F, 0x36]

    def test_one_chr_table_serves_the_hole_result_and_the_club_house(self, rom):
        table, _ = load_graphics_table(rom, 6, 0x8000)
        assert (table.dest_ppu_addr, table.end_ppu_addr) == (0x0000, 0x0F80)
        for name in ("hole_result", "club_house"):
            for scene in set_named(name).scenes:
                assert scene.chr_tables[0] == (6, 0x8000)

    def test_inventory(self, sprites):
        counts = {s.name: (len(s.frames), len(sprites.tiles(s))) for s in SETS}
        assert counts == {
            "club_house_walk_out": (7, 87),
            "club_house_walk_out_2p": (7, 184),
            "hole_result": (26, 171),
            "hole_result_2p": (25, 171),
            "club_house": (24, 130),
        }
