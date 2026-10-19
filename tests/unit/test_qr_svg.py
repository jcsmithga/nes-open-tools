"""The SVG QR code the site draws decodes to its text."""

import re

import pytest
from PIL import Image

from golf.qr import encoder
from golf.qr.decode import decode_zxing
from golf.qr.svg import code_svg

#: how many pixels a module becomes when the test rasterizes the SVG
SCALE = 8


def rasterize(svg: str) -> Image.Image:
    """Draw the SVG's module squares, as `code_svg` writes them, into an image."""
    viewbox = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert viewbox is not None
    side = int(viewbox.group(1))
    image = Image.new("L", (side * SCALE, side * SCALE), 255)
    path = re.search(r'<path d="([^"]*)"', svg)
    assert path is not None
    for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", path.group(1)):
        left, top = int(x) * SCALE, int(y) * SCALE
        image.paste(0, (left, top, left + SCALE, top + SCALE))
    return image


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/claim/abcdefghijklmnopqrstuv",
        "x" * encoder.MAX_CHARS,
    ],
)
def test_the_svg_decodes_to_its_text(text):
    assert decode_zxing(rasterize(code_svg(text))) == text


def test_the_svg_leaves_a_quiet_zone():
    svg = code_svg("https://example.com/claim/token")
    side = encoder.SIZE + 8
    assert f'viewBox="0 0 {side} {side}"' in svg
    corners = re.findall(r"M(\d+) (\d+)h1v1h-1z", svg)
    assert min(int(x) for x, _ in corners) == 4
    assert max(int(x) for x, _ in corners) == side - 5


def test_text_too_long_for_the_version_is_refused():
    with pytest.raises(ValueError, match="84"):
        code_svg("x" * (encoder.MAX_CHARS + 1))
