"""Installed images keep the part of the frame that matters for how they are shown."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from scripts.install_catalog_images import normalise

HEAD = (200, 40, 40)
BODY = (40, 40, 200)


def _tall_portrait(path: Path) -> None:
    """A 2:3 headshot stand-in: the top sixth is the head, the rest the body."""
    image = Image.new("RGB", (600, 900), BODY)
    image.paste(HEAD, (0, 0, 600, 150))
    image.save(path)


def _top_row_colour(path: Path) -> tuple[int, int, int]:
    with Image.open(path) as image:
        return image.convert("RGB").getpixel((image.width // 2, 2))


def test_portrait_crop_keeps_the_top_of_the_frame(tmp_path: Path) -> None:
    source, destination = tmp_path / "jordan-morgan.png", tmp_path / "out.jpg"
    _tall_portrait(source)

    normalise(source, destination, (300, 300), anchor_top=True)

    red, _, blue = _top_row_colour(destination)
    assert red > 150 and blue < 100


def test_catalog_crop_stays_centred(tmp_path: Path) -> None:
    source, destination = tmp_path / "WEL-002.png", tmp_path / "out.jpg"
    _tall_portrait(source)

    normalise(source, destination, (300, 300))

    red, _, blue = _top_row_colour(destination)
    assert blue > 150 and red < 100
