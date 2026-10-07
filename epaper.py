"""Reduce a rendered frame to the reTerminal E1002's six E Ink Spectra 6 inks.

The panel can only show black, white, red, yellow, blue and green. Handed a
full-colour PNG, the device firmware dithers the whole frame on its own, which
turns flat navy text and thin rules into a grainy blue-and-black speckle.
Doing the reduction here lets each region get the treatment it needs:

  * flat art and text snap to one solid ink per pixel with no dithering, so
    type and rules stay crisp;
  * photographic artwork (the pennants) is error-diffused, which is the only
    way six inks can suggest gold, maroon or teal.

Matching is done against ``PERCEIVED`` — roughly what each ink looks like on
the panel, which is far duller than its nominal RGB — so dithering mixes the
inks in proportions that read right on the glass. Pixels are then written out
as the pure ``DEVICE`` values, so the firmware's own palette lookup finds an
exact match and passes the frame through untouched.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# name: (pure value written to the PNG, approximate appearance on the panel)
INKS = {
    "black":  ((0, 0, 0),       (28, 32, 36)),
    "white":  ((255, 255, 255), (236, 236, 230)),
    "red":    ((255, 0, 0),     (190, 30, 34)),
    "yellow": ((255, 255, 0),   (240, 222, 70)),
    "blue":   ((0, 0, 255),     (40, 90, 190)),
    "green":  ((0, 255, 0),     (40, 110, 50)),
}
DEVICE = np.array([rgb for rgb, _ in INKS.values()], dtype=np.uint8)
PERCEIVED = np.array([rgb for _, rgb in INKS.values()], dtype=np.float32)
WHITE = list(INKS).index("white")

# How far a flat-art pixel must be from white before it takes ink. Just under
# half keeps anti-aliased strokes at about the weight they were drawn at, and
# keeps 1px rules and the footer's small type from breaking up.
INK_COVERAGE = 0.4


def _flat_indices(rgb: np.ndarray) -> np.ndarray:
    """One ink per pixel for type and line art, with no dithering.

    Every pixel is read as some ink laid over white paper at partial coverage,
    which is what an anti-aliased edge is. Its absorption (255 - rgb) is scaled
    up to full strength to recover the ink it came from, and that is matched
    against the non-white inks; the pixel then gets that ink if coverage
    clears INK_COVERAGE, white otherwise. Matching the plain pixel instead
    lets half-covered navy edges land on green or blue and fringes every glyph.
    """
    absorb = 255.0 - rgb
    coverage = absorb.max(axis=-1, keepdims=True)
    full = 255.0 - absorb * (255.0 / np.maximum(coverage, 1.0))

    inks = PERCEIVED.copy()
    inks[WHITE] = np.inf  # a fully-covered pixel is never paper
    dist = ((full[..., None, :] - inks) ** 2).sum(axis=-1)
    idx = dist.argmin(axis=-1)
    return np.where(coverage[..., 0] / 255.0 >= INK_COVERAGE, idx, WHITE)


def _dithered_indices(img: Image.Image) -> np.ndarray:
    flat = [int(c) for c in PERCEIVED.astype(np.uint8).ravel()]
    pal = Image.new("P", (1, 1))
    # Pillow wants 256 entries; repeating the six inks means no phantom
    # seventh colour can be picked, and `% 6` folds every index back.
    pal.putpalette((flat * 43)[: 768])
    q = img.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    return np.asarray(q) % len(INKS)


def to_spectra6(img: Image.Image, dither_mask: Image.Image | None = None) -> Image.Image:
    """Map `img` onto the six panel inks.

    `dither_mask` is an "L" image the size of `img`; non-zero pixels are
    error-diffused, the rest are treated as flat art. With no mask nothing is
    dithered.
    """
    rgb = img.convert("RGB")
    idx = _flat_indices(np.asarray(rgb, dtype=np.float32))
    if dither_mask is not None:
        art = np.asarray(dither_mask.convert("L")) > 0
        idx = np.where(art, _dithered_indices(rgb), idx)
    return Image.fromarray(DEVICE[idx], "RGB")
