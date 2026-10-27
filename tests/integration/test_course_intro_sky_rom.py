"""Integration tests: the course intro sky patch against both vanilla ROMs."""

from pathlib import Path

import pytest
from PIL import Image

from golf.core.graphics_codec import VideoMemory, load_graphics_table
from golf.core.palettes import NES_SYSTEM_PALETTE
from golf.core.patches import (
    MARIO_OPEN_SKY_IMAGE,
    PatchStack,
    course_intro_sky_patch,
    menu_trim_patch,
    read_mario_open_sky,
    read_sky_image,
    write_sky_image,
)
from golf.core.patches.course_intro_sky import (
    FIRST_FREE_TILE,
    GRAPHICS_BANK,
    JP_NAMETABLE_TABLE,
    JP_SKY_CHR_TABLE,
    LETTERS_STREAM_ADDR,
    LETTERS_STREAM_END,
    LETTERS_TABLE_ADDR,
    NAMETABLE_PTR_TABLE_ADDR,
    NAMETABLE_STREAM_ADDR,
    NAMETABLE_STREAM_END,
    NAMETABLE_TABLE_ADDR,
    ROW_WIDTH,
    SCENE_BANK,
    SKY_COLORS,
    SKY_ROWS,
    TILE_LIMIT,
    TILE_PTR_TABLE_ADDR,
)
from golf.core.patches.recipe import Recipe, RecipeStep
from golf.core.rom_reader import RomReader
from golf.core.rom_writer import RomWriter

US_ROM = "nes_open_us.nes"
JP_ROM = "mario_open_jp.nes"

pytestmark = pytest.mark.skipif(
    not (Path(US_ROM).exists() and Path(JP_ROM).exists()),
    reason=f"{US_ROM} and {JP_ROM} not both present",
)

SKY_CELLS = SKY_ROWS * ROW_WIDTH
#: the tile of the scene's sprite 0 (bank 10 `$AEA6`, docs/course_intro_scene.md)
SPRITE_ZERO_TILE = 0x1B


def scene(rom, course):
    """Video memory after the scene's tile and nametable loads for `course`."""
    vram = VideoMemory()
    for table in (TILE_PTR_TABLE_ADDR, NAMETABLE_PTR_TABLE_ADDR):
        pointer = rom.read_switched(table + 2 * course, SCENE_BANK, 2)
        load_graphics_table(rom, GRAPHICS_BANK, int.from_bytes(pointer, "little"), vram)
    return vram


def cell_patterns(vram, start, count):
    return [
        bytes(vram.data[tile * 16 : tile * 16 + 16])
        for tile in vram.data[0x2000 + start : 0x2000 + start + count]
    ]


@pytest.fixture(scope="module")
def sky():
    return read_mario_open_sky(RomReader(JP_ROM))


@pytest.fixture(scope="module")
def patched(sky, tmp_path_factory):
    out = tmp_path_factory.mktemp("sky") / "sky.nes"
    writer = RomWriter(US_ROM, str(out))
    patch = course_intro_sky_patch(sky)
    assert patch.can_apply(writer) and not patch.is_applied(writer)
    patch.apply(writer)
    assert patch.is_applied(writer)
    patch.apply(writer)  # a second apply is a no-op
    writer.save()
    return RomReader(str(out))


def test_the_sky_is_mario_opens_top_rows(sky):
    jp = RomReader(JP_ROM)
    vram = VideoMemory()
    load_graphics_table(jp, *JP_SKY_CHR_TABLE, vram)
    load_graphics_table(jp, *JP_NAMETABLE_TABLE, vram)
    assert list(sky.cells) == cell_patterns(vram, 0, SKY_CELLS)
    assert len(set(sky.cells)) == 98


@pytest.mark.parametrize("course", [0, 1, 2])
def test_every_course_draws_the_sky_over_the_vanilla_landscape(patched, sky, course):
    vanilla = scene(RomReader(US_ROM), 0)
    vram = scene(patched, course)
    assert cell_patterns(vram, 0, SKY_CELLS) == list(sky.cells)
    # rows 8-29 and the attribute table are the vanilla Japan screen's
    assert (
        vram.data[0x2000 + SKY_CELLS : 0x2400]
        == (vanilla.data[0x2000 + SKY_CELLS : 0x2400])
    )


def test_the_shared_tiles_and_the_portrait_tiles_are_untouched(patched):
    vanilla = scene(RomReader(US_ROM), 0)
    vram = scene(patched, 0)
    shared = FIRST_FREE_TILE * 16
    assert vram.data[:shared] == vanilla.data[:shared]
    # sprite 0 is one pixel of this tile, over a sky with no transparent pixel
    tile = vram.data[SPRITE_ZERO_TILE * 16 : SPRITE_ZERO_TILE * 16 + 16]
    assert tile == bytes([0] * 8 + [0x40] + [0] * 7)
    written = [addr for addr in vram.touched if addr < 0x2000]
    assert max(written) < TILE_LIMIT * 16


def test_the_new_streams_stay_inside_the_japan_ones(patched):
    table, _ = load_graphics_table(patched, GRAPHICS_BANK, LETTERS_TABLE_ADDR)
    letters = table.streams[1]
    assert letters.cpu_addr == LETTERS_STREAM_ADDR
    assert letters.cpu_addr + letters.compressed_length <= LETTERS_STREAM_END
    table, _ = load_graphics_table(patched, GRAPHICS_BANK, NAMETABLE_TABLE_ADDR)
    (nametable,) = table.streams
    assert nametable.cpu_addr == NAMETABLE_STREAM_ADDR
    assert nametable.cpu_addr + nametable.compressed_length <= NAMETABLE_STREAM_END
    assert nametable.end_ppu_addr == 0x2400

    vanilla = RomReader(US_ROM)
    for start, end in (
        (LETTERS_TABLE_ADDR, LETTERS_STREAM_ADDR),
        (LETTERS_STREAM_END, NAMETABLE_STREAM_ADDR),
        (NAMETABLE_STREAM_END, 0xC000),
    ):
        assert patched.read_switched(start, GRAPHICS_BANK, end - start) == (
            vanilla.read_switched(start, GRAPHICS_BANK, end - start)
        )


def test_it_stacks_with_menu_trim(sky):
    vanilla = Path(US_ROM).read_bytes()
    build = PatchStack([menu_trim_patch(), course_intro_sky_patch(sky)]).build(vanilla)
    assert len(build.regions["course_intro_sky"]) == 4


def test_the_checked_in_image_is_mario_opens_sky(sky, tmp_path):
    assert read_sky_image(MARIO_OPEN_SKY_IMAGE) == sky
    write_sky_image(sky, tmp_path / "sky.png")
    # pixels, not file bytes: the PNG's deflate stream depends on Pillow's zlib build
    with (
        Image.open(tmp_path / "sky.png") as fresh,
        Image.open(MARIO_OPEN_SKY_IMAGE) as checked_in,
    ):
        assert (fresh.mode, fresh.size) == (checked_in.mode, checked_in.size)
        assert fresh.tobytes() == checked_in.tobytes()


def recipe_build(params, base_dir):
    step = RecipeStep.from_dict({"patch": "course_intro_sky", **params}, base_dir)
    vanilla = Path(US_ROM).read_bytes()
    steps = [built.patch for built in Recipe([step]).build_steps(vanilla)]
    return scene(RomReader.from_bytes(PatchStack(steps).build(vanilla).rom), 2)


def test_the_recipe_step_draws_mario_opens_sky_by_default(sky):
    vram = recipe_build({}, Path("."))
    assert cell_patterns(vram, 0, SKY_CELLS) == list(sky.cells)


def test_the_recipe_step_takes_another_image(tmp_path):
    shade, white, blue = (NES_SYSTEM_PALETTE[color] for color in SKY_COLORS)
    image = Image.new("RGB", (256, 240), blue)
    image.paste(white, (8, 8, 24, 16))
    image.putpixel((8, 8), shade)
    image.save(tmp_path / "sky.png")
    vram = recipe_build({"image": "sky.png"}, tmp_path)
    assert cell_patterns(vram, 0, SKY_CELLS) == list(
        read_sky_image(tmp_path / "sky.png").cells
    )
