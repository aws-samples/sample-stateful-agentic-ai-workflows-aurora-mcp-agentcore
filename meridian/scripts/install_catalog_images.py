#!/usr/bin/env python3
"""Install generated catalog artwork into the showcase.

All 35 packages ship with commissioned artwork; nothing is fetched from a stock
photo CDN. This script takes freshly generated images, normalises them to the
hero's aspect and pixel target, and drops them into
``frontend/public/travel/catalog/``.

The hero renders around 1743x1050 CSS px, which is about 3500 device pixels on
a 2x display. Sources below that get enlarged and sharpened rather than
rejected, and the run reports which ones were short.

Usage::

    python scripts/install_catalog_images.py ~/Downloads/meridian-art
    python scripts/install_catalog_images.py ~/Downloads/meridian-art --dry-run
    python scripts/install_catalog_images.py ~/Downloads --map IMG_4821.jpg=WEL-002

A file is matched when its name starts with a package id - ``WEL-002.png`` and
``WEL-002 - Amalfi Coast Villa Week.png`` both work. ``--map`` covers anything
that does not, such as a camera roll filename.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover - guidance beats a traceback
    sys.exit("Pillow is required: pip install Pillow")

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "frontend" / "public" / "travel" / "catalog"
PORTRAIT = REPO / "frontend" / "public" / "travel"

MISSING_ART_HINT = (
    "Every package in the catalog ships with its own artwork, so a gap here is "
    "a card that will fall back to a flat gradient on stage."
)

# The full catalog: seven series of five. Kept in step with TRIP_PACKAGES in
# travel_catalog.py - if a package is added there it needs artwork here.
PACKAGE_IDS = [
    "ADV-001", "ADV-002", "ADV-003", "ADV-004", "ADV-005",
    "BCH-001", "BCH-002", "BCH-003", "BCH-004", "BCH-005",
    "BIZ-001", "BIZ-002", "BIZ-003", "BIZ-004", "BIZ-005",
    "CTY-001", "CTY-002", "CTY-003", "CTY-004", "CTY-005",
    "FAM-001", "FAM-002", "FAM-003", "FAM-004", "FAM-005",
    "TKY-001", "TKY-002", "TKY-003", "TKY-004", "TKY-005",
    "WEL-001", "WEL-002", "WEL-003", "WEL-004", "WEL-005",
]

# What the hero actually needs at 2x. Anything smaller is accepted but flagged,
# because a soft hero is exactly the problem this script exists to fix.
TARGET_WIDTH = 2560
TARGET_HEIGHT = 1440
MIN_WIDTH = 2200
JPEG_QUALITY = 82

# Sharpening applied only when a source had to be enlarged. Spreading fixed
# detail over more pixels is what makes an upscale read soft, and an unsharp
# mask puts back most of the local contrast that costs. The threshold keeps it
# off smooth gradients, so skies stay clean instead of picking up halos.
# Downscaled sources skip this: Lanczos reduction is already crisp and the
# mask would only over-bite the edges.
UNSHARP_RADIUS = 1.5
UNSHARP_PERCENT = 80
UNSHARP_THRESHOLD = 3

# Alex is centre-cropped into a circle, so a square source is what keeps the
# framing under your control rather than the crop's.
PORTRAIT_TARGETS = {"alex-morgan": (1024, 1024)}


def resolve_target(stem: str) -> tuple[Path, tuple[int, int]] | None:
    """Map a file stem onto its destination and pixel target.

    Accepts a bare package id (``WEL-002``) and also the shape image tools
    hand back when the prompt is used as the filename - a leading id followed
    by a separator and the package name, e.g. ``TKY-005 - Tokyo Autumn Koyo``.
    Renaming 35 files by hand before every install is exactly the sort of
    chore that gets one of them wrong.
    """
    cleaned = stem.strip()
    key = cleaned.upper()
    if key in PACKAGE_IDS:
        return CATALOG / f"{key}.jpg", (TARGET_WIDTH, TARGET_HEIGHT)

    leading = re.match(r"^([A-Z]{3}-\d{3})\b", key)
    if leading and leading.group(1) in PACKAGE_IDS:
        package = leading.group(1)
        return CATALOG / f"{package}.jpg", (TARGET_WIDTH, TARGET_HEIGHT)

    lowered = cleaned.lower()
    if lowered in PORTRAIT_TARGETS:
        return PORTRAIT / f"{lowered}.jpg", PORTRAIT_TARGETS[lowered]
    return None


def normalise(source: Path, destination: Path, size: tuple[int, int]) -> str:
    """Cover-crop to the target aspect, downscale, and save as progressive JPEG."""
    with Image.open(source) as image:
        image = image.convert("RGB")
        src_w, src_h = image.size
        target_w, target_h = size

        # Cover: scale so the image fills the frame, then centre-crop the excess.
        scale = max(target_w / src_w, target_h / src_h)
        interim = (max(1, round(src_w * scale)), max(1, round(src_h * scale)))
        image = image.resize(interim, Image.Resampling.LANCZOS)
        left = (interim[0] - target_w) // 2
        top = (interim[1] - target_h) // 2
        image = image.crop((left, top, left + target_w, top + target_h))

        # Enlarging spreads fixed detail over more pixels, which is what reads
        # as soft. An unsharp mask restores most of the local contrast that
        # costs; a reduction is already crisp, so it skips this.
        enlarged = scale > 1.0
        if enlarged:
            image = image.filter(
                ImageFilter.UnsharpMask(
                    radius=UNSHARP_RADIUS,
                    percent=UNSHARP_PERCENT,
                    threshold=UNSHARP_THRESHOLD,
                )
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(
            destination,
            "JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
    kb = destination.stat().st_size // 1024
    note = " +sharpen" if enlarged else ""
    return f"{src_w}x{src_h} -> {target_w}x{target_h}, {kb} KB{note}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="folder holding the new images")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="FILE=PACKAGE_ID",
        help="assign a file to a package id, e.g. IMG_4821.jpg=WEL-002",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen without writing anything",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="copy the images being replaced into this folder first",
    )
    args = parser.parse_args()

    if not args.source.is_dir():
        return f"not a folder: {args.source}"  # type: ignore[return-value]

    explicit: dict[str, str] = {}
    for entry in args.map:
        if "=" not in entry:
            print(f"skipping malformed --map {entry!r}; expected FILE=PACKAGE_ID")
            continue
        filename, package = entry.split("=", 1)
        explicit[filename.strip()] = package.strip()

    candidates = sorted(
        path
        for path in args.source.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}
    )
    if not candidates:
        print(f"no images found in {args.source}")
        return 1

    installed, skipped, warnings = 0, [], []
    for path in candidates:
        stem = explicit.get(path.name, path.stem)
        target = resolve_target(stem)
        if target is None:
            skipped.append(path.name)
            continue
        destination, size = target

        try:
            with Image.open(path) as probe:
                width, height = probe.size
        except Exception as exc:  # noqa: BLE001 - report and move on
            warnings.append(f"{path.name}: unreadable ({exc})")
            continue

        if destination.parent == CATALOG and width < MIN_WIDTH:
            warnings.append(
                f"{path.name}: {width}px wide. The hero needs {MIN_WIDTH}px+ at 2x, "
                f"so this will still look soft."
            )

        if args.dry_run:
            print(f"  would install {path.name:28s} -> {destination.name:16s} ({width}x{height})")
            installed += 1
            continue

        if args.backup and destination.exists():
            args.backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, args.backup / destination.name)

        detail = normalise(path, destination, size)
        print(f"  {path.name:28s} -> {destination.name:16s}  {detail}")
        installed += 1

    print(f"\n{installed} installed, {len(skipped)} unmatched")
    if skipped:
        print("\nUnmatched files need a --map entry:")
        for name in skipped:
            print(f"  --map {name}=PACKAGE_ID")
        print(f"\nValid package ids: {', '.join(PACKAGE_IDS)}")
    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  {warning}")

    missing = [
        package for package in PACKAGE_IDS
        if not (CATALOG / f"{package}.jpg").exists()
    ]
    if missing:
        print(f"\nStill missing artwork: {', '.join(missing)}")
        print(f"  {MISSING_ART_HINT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
