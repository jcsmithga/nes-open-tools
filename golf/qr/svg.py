"""
SVG rendering of a QR code, for a web page to draw inline.

The site's hosting page draws its claim link with this, so it needs no QR package at
runtime (`qrcode` is a dev dependency only). The text must fit the encoder's one version:
`encoder.MAX_CHARS` bytes. Unlike the ROM, which uses a fixed mask, this picks the mask the
spec would, since it costs nothing here.
"""

from golf.qr import encoder
from golf.qr.nes import QUIET_MODULES


def code_svg(text: str, quiet_modules: int = QUIET_MODULES) -> str:
    """The code for `text` as an SVG element, one unit per module, scaled by its container.

    Raises ValueError for text longer than `encoder.MAX_CHARS` bytes.
    """
    data = text.encode()
    if len(data) > encoder.MAX_CHARS:
        raise ValueError(
            f"{len(data)} bytes is over the {encoder.MAX_CHARS} a version 5 code holds"
        )
    matrix = encoder.encode(data, encoder.best_mask(data))
    side = matrix.size + 2 * quiet_modules
    path = "".join(
        f"M{col + quiet_modules} {row + quiet_modules}h1v1h-1z"
        for row in range(matrix.size)
        for col in range(matrix.size)
        if matrix.get(row, col)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" '
        f'shape-rendering="crispEdges" role="img">'
        f'<rect width="{side}" height="{side}" fill="#fff"/>'
        f'<path d="{path}" fill="#000"/></svg>'
    )
