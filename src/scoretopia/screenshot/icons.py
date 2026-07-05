"""Avatar icon analysis for Polytopia screenshot extraction."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def is_skull_avatar(
    image: str | Path | Image.Image,
    *,
    bbox: tuple[int, int, int, int] | None = None,
) -> bool:
    """Return True when a cropped avatar region looks like a grey elimination skull."""
    from PIL import Image as PILImage

    if isinstance(image, (str, Path)):
        with PILImage.open(image) as opened:
            region = opened.convert("RGB")
            if bbox is not None:
                region = region.crop(bbox)
            return _is_grey_skull(region)

    region = image.convert("RGB")
    if bbox is not None:
        region = region.crop(bbox)
    return _is_grey_skull(region)


def _is_grey_skull(region: Image.Image) -> bool:
    spreads = [
        max(red, green, blue) - min(red, green, blue)
        for red, green, blue in region.get_flattened_data()
    ]
    if not spreads:
        return False
    mean_spread = sum(spreads) / len(spreads)
    return mean_spread < 3.0


def crop_avatar_region(
    image: Image.Image,
    *,
    row_y: float,
    icon_size: int = 70,
    icon_x: int = 30,
) -> Image.Image:
    """Crop the player avatar slot aligned to an OCR row center."""
    top = int(row_y - icon_size / 2)
    return image.crop((icon_x, top, icon_x + icon_size, top + icon_size))
