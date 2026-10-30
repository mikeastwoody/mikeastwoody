#!/usr/bin/env python3
"""Rebuild the embedded font subsets in assets/fonts/.

One-off maintenance tool, not used by the daily workflow. It needs fontTools and
brotli (`pip install fonttools brotli`) and the variable fonts from Google Fonts:

    scripts/subset_fonts.py InterTight[wght].ttf JetBrainsMono[wght].ttf

It writes one WOFF2 subset per weight and metrics.json, which holds the advance
widths and vertical metrics that build_profile.py uses to lay out text.
"""

import json
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "fonts"

# Basic Latin, the Spanish letters and the punctuation the panel uses.
CHARSET = (
    [chr(c) for c in range(0x20, 0x7F)]
    + list("\u00a0¡¿·áéíóúüñÁÉÍÓÚÜÑ–—‘’“”•…●")
)

# JetBrains Mono drops its code ligatures (calt), which nothing in the panel needs.
FACES = {
    "InterTight": ("Inter Tight", (400, 600, 700), None),
    "JetBrainsMono": ("JetBrains Mono", (400, 500), ["ccmp", "locl", "mark", "mkmk"]),
}


def build(variable_path):
    stem = Path(variable_path).name.split("[")[0]
    family, weights, features = FACES[stem]
    faces = {}
    for weight in weights:
        font = instancer.instantiateVariableFont(TTFont(variable_path), {"wght": weight})
        options = subset.Options()
        options.flavor = "woff2"
        options.hinting = False
        options.desubroutinize = True
        options.name_IDs = [0, 1, 2, 4, 6, 13, 14]
        options.notdef_outline = True
        if features is not None:
            options.layout_features = features
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=[ord(c) for c in CHARSET])
        subsetter.subset(font)
        name = f"{stem}-{weight}.woff2"
        subset.save_font(font, str(OUT / name), options)

        cmap = font.getBestCmap()
        hmtx = font["hmtx"]
        faces[f"{family} {weight}"] = {
            "file": name,
            "family": family,
            "weight": weight,
            "upm": font["head"].unitsPerEm,
            "ascent": font["hhea"].ascent,
            "descent": -font["hhea"].descent,
            "advances": {str(cp): hmtx[glyph][0] for cp, glyph in sorted(cmap.items())},
        }
    return faces


def main(paths):
    OUT.mkdir(parents=True, exist_ok=True)
    metrics = {}
    for path in paths:
        metrics.update(build(path))
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
