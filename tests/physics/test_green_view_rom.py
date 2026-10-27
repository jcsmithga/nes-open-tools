"""
The putting view (`golf.physics.green_view`) against the game.

- The aim every shot starts with, against `CalcAimTowardFlag` run alone.
- The ball, cup and aim cursor, against the sprites the game itself leaves in
  RAM on the first frame of the swing (`green_view_sprites`), for random spots
  on the green, aims and pins.
- The power meter's marker, against a putt played through the game.
- Putts from spots between pixels, frame by frame against `RomGameShot`, which
  the other putting checks (`test_game_rom.py`) only start on whole pixels.
"""

import random
from pathlib import Path

import pytest

from golf.core.rom_reader import RomReader
from golf.formats.hole_data import HoleData
from golf.physics import PUTTER, ShotInput, Spin
from golf.physics.green_view import (
    METER_Y,
    AimTables,
    aim_at_flag,
    cup_position,
    cursor_position,
    meter_marker_x,
    pin_distance,
    place,
    read_metasprites,
    screen_position,
)
from golf.physics.rom_game import RomGameShot, Swing, green_view_sprites
from golf.physics.rom_oracle import RomAimAtFlag
from golf.physics.shot import ShotInFlight
from golf.physics.state import Lie
from golf.physics.tables import PhysicsTables
from golf.physics.terrain import HoleGround, TerrainTables

ROM_PATH = "nes_open_us.nes"

pytestmark = pytest.mark.skipif(
    not Path(ROM_PATH).exists(), reason=f"{ROM_PATH} not present"
)


@pytest.fixture(scope="module")
def rom() -> RomReader:
    return RomReader(ROM_PATH)


@pytest.fixture(scope="module")
def physics(rom) -> PhysicsTables:
    return PhysicsTables.from_rom(rom)


@pytest.fixture(scope="module")
def terrain(rom) -> TerrainTables:
    return TerrainTables.from_rom(rom)


@pytest.fixture(scope="module")
def aim_tables(rom) -> AimTables:
    return AimTables.from_rom(rom)


def test_aim_at_flag(rom, aim_tables):
    rng = random.Random(0)
    oracle = RomAimAtFlag(rom)
    flag = (0x5AC8, 0x5488)
    cases = [
        # On the flag, level with it, straight above, and on each diagonal.
        flag,
        (flag[0] - 0x300, flag[1]),
        (flag[0], flag[1] + 0x300),
        (flag[0] + 0x300, flag[1] + 0x300),
        (flag[0] - 0x300, flag[1] - 0x300),
    ]
    for _ in range(3000):
        far = rng.random() < 0.3
        reach = 0x6000 if far else 0x1800
        cases.append(
            (
                (flag[0] + rng.randint(-reach, reach)) & 0xFFFF,
                max(0, flag[1] + rng.randint(-reach, 4 * reach if far else reach)),
            )
        )
    for ball_x, ball_y in cases:
        model = aim_at_flag(ball_x, ball_y, *flag, aim_tables)
        assert model == oracle.aim(ball_x, ball_y, *flag), (
            hex(ball_x),
            hex(ball_y),
        )


def load_hole(courses: Path, course: str, number: int) -> HoleData:
    hole = HoleData()
    hole.load(courses / course / f"hole_{number:02}.json")
    return hole


def on_the_green(ground: HoleGround, x: int, y: int) -> bool:
    return ground.classify(x >> 8, x & 0xFF, y >> 8, y & 0xFF).lie == Lie.GREEN


def random_spot(
    rng: random.Random, hole: HoleData, ground: HoleGround
) -> tuple[int, int]:
    while True:
        x = (hole.green_x << 8) + rng.randrange(24 << 8)
        y = (hole.green_y << 8) + rng.randrange(24 << 8)
        if on_the_green(ground, x, y):
            return x, y


def model_sprites(
    rom, physics, hole: HoleData, pin: int, x: int, y: int, aim: int
) -> list[tuple[int, int, int, int]]:
    """The ball, cup and cursor where `green_view` puts them."""
    sprites = read_metasprites(rom)
    position = hole.metadata["flag_positions"][pin]
    cup = cup_position(position["x_offset"], position["y_offset"])
    ball = screen_position(x, y, hole.green_x, hole.green_y)
    assert ball is not None
    cursor = cursor_position(ball, aim, pin_distance(ball, cup), physics)
    return sorted(
        place(sprites["ball"], ball, clipped=True)
        + place(sprites["cup"], cup, clipped=False)
        + place(sprites["cursor"], cursor, clipped=True)
    )


DYNAMIC = ("ball", "cup", "cursor")


def assert_sprites_match(rom, physics, terrain, hole: HoleData, seed: int) -> None:
    rng = random.Random(seed)
    sprites = read_metasprites(rom)
    tiles = {tile for name in DYNAMIC for _, tile, _, _ in sprites[name].sprites}
    pin = rng.randrange(4)
    x, y = random_spot(rng, hole, HoleGround(hole, terrain))
    aim = rng.randrange(256)
    game = sorted(
        s for s in green_view_sprites(rom, hole, pin, (x, y), aim) if s[1] in tiles
    )
    model = model_sprites(rom, physics, hole, pin, x, y, aim)
    assert model == game, (pin, hex(x), hex(y), hex(aim))


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("course,number", [("us", 1), ("japan", 7), ("uk", 12)])
def test_nes_open_sprites(rom, physics, terrain, vanilla_courses, course, number, seed):
    hole = load_hole(vanilla_courses, course, number)
    assert_sprites_match(rom, physics, terrain, hole, seed)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("course,number", [("jp_hawaii", 7), ("jp_france", 3)])
def test_mario_open_sprites(
    rom, physics, terrain, vanilla_jp_courses, course, number, seed
):
    hole = load_hole(vanilla_jp_courses / "jp", course, number)
    assert_sprites_match(rom, physics, terrain, hole, seed)


@pytest.mark.parametrize("seed", range(6))
def test_meter_marker(rom, seed):
    rng = random.Random(seed)
    game = RomGameShot(rom, "us", 1)
    flag_x, flag_y = game.flag.x >> 8, game.flag.y >> 8
    shot = ShotInput(
        club=PUTTER, swing_speed=rng.randrange(3), x=flag_x + 4, y=flag_y + 3
    )
    record = game.play(shot, Swing(start=4, power=rng.randint(4, 60)))
    marker = read_metasprites(rom)["marker"]
    page = game.machine.memory[0x200:0x300]
    oam = [tuple(page[i : i + 4]) for i in range(0, 256, 4)]
    model = place(marker, (meter_marker_x(record.shot.power_stop), METER_Y), False)
    assert model[0] in oam


@pytest.mark.parametrize("seed", range(8))
def test_putts_between_pixels(rom, physics, terrain, vanilla_courses, seed):
    """A putt from a spot with fractions, frame by frame."""
    rng = random.Random(seed)
    hole = load_hole(vanilla_courses, "us", 1)
    ground = HoleGround(hole, terrain)
    game = RomGameShot(rom, "us", 1, rng_state=rng.randrange(0x10000))
    x, y = random_spot(rng, hole, ground)
    shot = ShotInput(
        club=PUTTER,
        swing_speed=rng.randrange(3),
        aim=rng.randrange(256),
        spin=Spin.TOP_2,
        x=x >> 8,
        y=y >> 8,
        x_fraction=x & 0xFF,
        y_fraction=y & 0xFF,
    )
    record = game.play(shot, Swing(start=4, power=rng.randint(4, 60)))
    flight = ShotInFlight(record.shot, ground, physics, flag=game.flag)
    ball = flight.ball
    frames = [(ball.x, ball.y)]
    while not ball.stopped:
        flight.step()
        frames.append((ball.x, ball.y))
    assert frames == [(b.x, b.y) for b in record.frames]
    assert bool(ball.holed) == bool(record.frames[-1].holed)


@pytest.mark.parametrize("aim", [0x00, 0x20, 0xA0])
def test_cursor_from_the_far_corner(rom, physics, vanilla_courses, aim):
    """
    `PinDistance` keeps the distance's low byte, so a ball 256 or more green-view
    pixels from the cup, corner to corner of a green that is putting surface
    edge to edge, gets the near cursor.
    """
    hole = load_hole(vanilla_courses, "us", 1)
    hole.greens = [[0x60] * 24 for _ in range(24)]
    hole.metadata["flag_positions"][0] = {"x_offset": 2, "y_offset": 2}
    x, y = (hole.green_x + 23) << 8 | 0xE0, (hole.green_y + 23) << 8 | 0xE0
    cursor = read_metasprites(rom)["cursor"]
    tile = cursor.sprites[0][1]
    game = [s for s in green_view_sprites(rom, hole, 0, (x, y), aim) if s[1] == tile]
    model = [s for s in model_sprites(rom, physics, hole, 0, x, y, aim) if s[1] == tile]
    assert model == game
