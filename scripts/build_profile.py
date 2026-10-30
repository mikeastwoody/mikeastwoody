#!/usr/bin/env python3
"""Regenerate the production panel of the profile README.

Reads profile.json (App Store ids, display names, subtitles and the two cards),
asks the iTunes Lookup API for each app's version, release date and icon, and
rewrites assets/profile/*.svg and the generated blocks of README.md.

Standard library only: the daily workflow runs it with the runner's python3.
GitHub shows README images through <img>, which cannot load anything external,
so every SVG embeds its fonts (assets/fonts) and its icon as data: URIs.
"""

import base64
import hashlib
import html
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "profile.json"
README = ROOT / "README.md"
FONTS = ROOT / "assets" / "fonts"
OUT = ROOT / "assets" / "profile"
ICONS = OUT / "icons"

LOOKUP_URL = "https://itunes.apple.com/lookup"
STORE_URL = "https://apps.apple.com/app/id{}"
WORKFLOW = ".github/workflows/refresh-production.yml"
USER_AGENT = "mikeastwoody-profile-refresh (+https://github.com/mikeastwoody/mikeastwoody)"
ICON_SIZE = 144  # 4x the 36 px the row shows

# Geometry, colors and copy from the approved design (Estado.dc.html), measured
# with the panel at 846 px: the profile README's width on desktop.
WIDTH = 846
ROW_H = 64
FOOT_H = 44
CARD_W = 417
CARD_GAP = 12
VERSION_X = 433
DATE_X = 547
END_X = WIDTH - 29
NAME_MAX = VERSION_X - 12 - 28 - 77

PANEL = "#0a0d12"
LINE = "#1f2630"
TEXT = "#f5f7fa"
BODY = "#e6edf3"
MUTED = "#9aa4b2"
GREEN = "#3fb950"
AMBER = "#d29922"
CARD_THEMES = {
    "dark": {"border": "#30363d", "title": "#e6edf3", "muted": "#8b949e"},
    "light": {"border": "#d1d9e0", "title": "#1f2328", "muted": "#59636e"},
}

TITLE = "Hi, I'm Mike."
INTRO = (
    "Seven years keeping production up as an SRE / DevOps engineer. "
    "Now the production is mine: iOS apps I design, build and ship alone."
)
FOOT = "refreshed daily from the App Store by a GitHub Action"

SANS = '"Inter Tight",-apple-system,"Segoe UI",Helvetica,Arial,sans-serif'
MONO = '"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'


class Face:
    """One embedded font weight: its WOFF2 file and the metrics to lay text out."""

    def __init__(self, data):
        self.family = data["family"]
        self.weight = data["weight"]
        self.file = FONTS / data["file"]
        self.upm = data["upm"]
        self.ascent = data["ascent"]
        self.descent = data["descent"]
        self.advances = {int(cp): adv for cp, adv in data["advances"].items()}

    def width(self, text, size, spacing=0.0):
        missing = sorted({c for c in text if ord(c) not in self.advances})
        if missing:
            raise ValueError(
                f"{self.family} {self.weight} has no glyph for {missing}; "
                "add it to CHARSET in scripts/subset_fonts.py"
            )
        return sum(self.advances[ord(c)] for c in text) * size / self.upm + spacing * len(text)

    def baseline(self, size, line_height):
        """Baseline offset inside a CSS line box, rounded the way Chrome does."""
        ascent = math.floor(self.ascent * size / self.upm + 0.5)
        descent = math.floor(self.descent * size / self.upm + 0.5)
        return math.floor((line_height - ascent - descent) / 2) + ascent

    def css(self):
        data = base64.b64encode(self.file.read_bytes()).decode()
        return (
            f'@font-face{{font-family:"{self.family}";font-weight:{self.weight};'
            f'src:url(data:font/woff2;base64,{data}) format("woff2")}}'
        )


FACES = {
    name: Face(data)
    for name, data in json.loads((FONTS / "metrics.json").read_text()).items()
}
SANS_400 = FACES["Inter Tight 400"]
SANS_600 = FACES["Inter Tight 600"]
SANS_700 = FACES["Inter Tight 700"]
MONO_400 = FACES["JetBrains Mono 400"]
MONO_500 = FACES["JetBrains Mono 500"]


@dataclass
class App:
    id: int
    name: str
    subtitle: str
    version: str
    released: datetime
    artwork: str

    @property
    def date(self):
        return self.released.strftime("%Y-%m-%d")

    @property
    def url(self):
        return STORE_URL.format(self.id)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except OSError as err:
            if attempt == 2:
                raise SystemExit(f"GET {url} failed: {err}")
            time.sleep(10 * (attempt + 1))


def lookup(ids, country):
    query = urllib.parse.urlencode({"id": ",".join(map(str, ids)), "country": country})
    data = json.loads(fetch(f"{LOOKUP_URL}?{query}"))
    results = {r["trackId"]: r for r in data.get("results", []) if r.get("trackId") in ids}
    # An empty answer is far more likely an API hiccup than every app gone at once.
    if not results:
        raise SystemExit("The iTunes Lookup API returned none of the apps; panel left as it is")
    return results


def collect(configured, results):
    """Apps the API returned, newest release first. Anything else is not live."""
    apps = []
    for entry in configured:
        result = results.get(entry["id"])
        if not result or not all(result.get(k) for k in ("version", "currentVersionReleaseDate", "artworkUrl512")):
            continue
        released = datetime.fromisoformat(result["currentVersionReleaseDate"].replace("Z", "+00:00"))
        apps.append(App(entry["id"], entry["name"], entry["subtitle"], result["version"], released, result["artworkUrl512"]))
    return sorted(apps, key=lambda app: app.released, reverse=True)


def icon(app):
    """The app icon as JPEG bytes, cached by artwork URL so unchanged icons stay byte-identical."""
    key = hashlib.sha1(app.artwork.encode()).hexdigest()[:12]
    path = ICONS / f"{app.id}-{key}.jpg"
    if not path.exists():
        url = app.artwork.rsplit("/", 1)[0] + f"/{ICON_SIZE}x{ICON_SIZE}bb.jpg"
        ICONS.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch(url))
    return path


def num(value):
    return f"{round(value, 2):g}"


def text(x, y, content, face, size, fill, anchor=None, spacing=None):
    family = "m" if face.family == "JetBrains Mono" else "s"
    extra = f' text-anchor="{anchor}"' if anchor else ""
    extra += f' letter-spacing="{num(spacing)}"' if spacing else ""
    return (
        f'<text class="{family}" x="{num(x)}" y="{num(y)}" font-size="{num(size)}" '
        f'font-weight="{face.weight}" fill="{fill}"{extra}>{escape(content)}</text>'
    )


def hline(y):
    return f'<rect x="1" y="{y}" width="{WIDTH - 2}" height="1" fill="{LINE}"/>'


def svg(width, height, faces, body):
    style = "".join(face.css() for face in faces) + f".s{{font-family:{SANS}}}.m{{font-family:{MONO}}}"
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f"<style>{style}</style>",
            *body,
            "</svg>",
            "",
        ]
    )


def fit(content, face, size, max_width):
    if face.width(content, size) <= max_width:
        return content
    while content and face.width(content + "…", size) > max_width:
        content = content[:-1]
    return content.rstrip() + "…"


def wrap(content, face, size, max_width):
    lines, line = [], ""
    for word in content.split():
        candidate = f"{line} {word}".strip()
        if line and face.width(candidate, size) > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + [line]


def status(live, total):
    return f"{live} of {total} apps live"


def head_svg(live, total):
    intro = wrap(INTRO, SANS_400, 18, 620)
    divider = 140 + 27 * len(intro) + 26
    height = divider + 39
    body = [
        # Rounded top corners; the bottom edge of this rect falls outside the image.
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height + 40}" rx="15.5" fill="{PANEL}" stroke="{LINE}"/>',
        f'<circle cx="27" cy="24.75" r="4" fill="{GREEN if live == total else AMBER}"/>',
        text(41, 15 + MONO_400.baseline(13, 19.5), "mikeastwood / production", MONO_400, 13, MUTED),
        text(WIDTH - 23, 15 + MONO_400.baseline(13, 19.5), status(live, total), MONO_400, 13, MUTED, anchor="end"),
        hline(49),
        text(29, 82 + SANS_700.baseline(44, 46.2), TITLE, SANS_700, 44, TEXT, spacing=-0.03 * 44),
        *(
            text(29, 140 + 27 * i + SANS_400.baseline(18, 27), line, SANS_400, 18, MUTED)
            for i, line in enumerate(intro)
        ),
        hline(divider),
    ]
    th_y = divider + 11 + MONO_500.baseline(12, 18)
    body += [
        text(29, th_y, "APP", MONO_500, 12, MUTED),
        text(VERSION_X, th_y, "VERSION", MONO_500, 12, MUTED),
        text(DATE_X, th_y, "LAST RELEASE", MONO_500, 12, MUTED),
        text(END_X, th_y, "STATUS", MONO_500, 12, MUTED, anchor="end"),
    ]
    return svg(WIDTH, height, [SANS_700, SANS_400, MONO_400, MONO_500], body)


def row_svg(app, icon_path):
    data = base64.b64encode(icon_path.read_bytes()).decode()
    cell_y = 22 + MONO_400.baseline(14, 21)
    body = [
        # The border color underneath draws the divider above and both side borders.
        f'<rect width="{WIDTH}" height="{ROW_H}" fill="{LINE}"/>',
        f'<rect x="1" y="1" width="{WIDTH - 2}" height="{ROW_H - 1}" fill="{PANEL}"/>',
        '<clipPath id="icon"><rect x="29" y="14.5" width="36" height="36" rx="9"/></clipPath>',
        f'<image x="29" y="14.5" width="36" height="36" clip-path="url(#icon)" href="data:image/jpeg;base64,{data}"/>',
        text(77, 13 + SANS_600.baseline(14, 21), fit(app.name, SANS_600, 14, NAME_MAX), SANS_600, 14, TEXT),
        text(77, 34 + SANS_400.baseline(12, 18), fit(app.subtitle, SANS_400, 12, NAME_MAX), SANS_400, 12, MUTED),
        text(VERSION_X, cell_y, fit(app.version, MONO_400, 14, DATE_X - VERSION_X - 12), MONO_400, 14, BODY),
        text(DATE_X, cell_y, app.date, MONO_400, 14, MUTED),
        text(END_X, cell_y, "● live", MONO_400, 14, GREEN, anchor="end"),
    ]
    return svg(WIDTH, ROW_H, [SANS_600, SANS_400, MONO_400], body)


def foot_svg():
    body = [
        # Rounded bottom corners; the top edge of this rect falls outside the image.
        f'<rect x="0.5" y="-40" width="{WIDTH - 1}" height="{FOOT_H + 40 - 0.5}" rx="15.5" fill="{PANEL}" stroke="{LINE}"/>',
        hline(0),
        text(29, 13 + MONO_400.baseline(12, 18), FOOT, MONO_400, 12, MUTED),
    ]
    return svg(WIDTH, FOOT_H, [MONO_400], body)


def card_lines(card):
    return wrap(card["text"], SANS_400, 13, CARD_W - 34)


def card_svg(card, theme, lines, width):
    colors = CARD_THEMES[theme]
    height = math.ceil(17 + 18 + 4 + 22.5 + 4 + 19.5 * lines + 17)
    body = [
        f'<rect x="0.5" y="0.5" width="{CARD_W - 1}" height="{height - 1}" rx="11.5" fill="none" stroke="{colors["border"]}"/>',
        text(17, 17 + MONO_400.baseline(12, 18), card["label"], MONO_400, 12, colors["muted"]),
        text(17, 39 + SANS_600.baseline(15, 22.5), card["title"], SANS_600, 15, colors["title"]),
        *(
            text(17, 65.5 + 19.5 * i + SANS_400.baseline(13, 19.5), line, SANS_400, 13, colors["muted"])
            for i, line in enumerate(card_lines(card))
        ),
    ]
    return svg(width, height, [MONO_400, SANS_600, SANS_400], body)


def img(src, width, alt):
    return f'<img src="{src}" width="{width}" align="top" alt="{html.escape(alt)}">'


def panel_block(apps, total):
    head_alt = f"mikeastwood / production: {status(len(apps), total)}. {TITLE} {INTRO}"
    lines = ["<p>", img("assets/profile/head.svg", WIDTH, head_alt) + "<br>"]
    for app in apps:
        alt = f"{app.name} — {app.subtitle} — version {app.version}, last release {app.date}, live on the App Store"
        lines.append(f'<a href="{app.url}">{img(f"assets/profile/app-{app.id}.svg", WIDTH, alt)}</a><br>')
    lines += [f'<a href="{WORKFLOW}">{img("assets/profile/foot.svg", WIDTH, FOOT[0].upper() + FOOT[1:])}</a>', "</p>"]
    return "\n".join(lines)


def cards_block(cards):
    links = []
    for i, card in enumerate(cards):
        # The gap between the cards is transparent space on the right of every card but the last.
        width = CARD_W + (CARD_GAP if i < len(cards) - 1 else 0)
        alt = f'{card["label"][0].upper()}{card["label"][1:]}: {card["title"]}. {card["text"]}'
        base = f'assets/profile/card-{card["slug"]}'
        links.append(
            f'<a href="{card["url"]}"><picture>'
            f'<source media="(prefers-color-scheme: dark)" srcset="{base}-dark.svg">'
            f'<img src="{base}-light.svg" width="{width}" alt="{html.escape(alt)}">'
            "</picture></a>"
        )
    return "".join(links)


def replace_block(readme, name, content):
    pattern = re.compile(rf"(<!-- {name}:start -->).*?(<!-- {name}:end -->)", re.S)
    if not pattern.search(readme):
        raise SystemExit(f"README.md has no <!-- {name}:start --> ... <!-- {name}:end --> block")
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(2)}", readme)


def write(path, content):
    data = content.encode() if isinstance(content, str) else content
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def build(config, results):
    apps = collect(config["apps"], results)
    total = len(config["apps"])
    files = {OUT / "head.svg": head_svg(len(apps), total), OUT / "foot.svg": foot_svg()}
    icons = set()
    for app in apps:
        icon_path = icon(app)
        icons.add(icon_path)
        files[OUT / f"app-{app.id}.svg"] = row_svg(app, icon_path)
    cards = config["cards"]
    lines = max(len(card_lines(card)) for card in cards)
    for i, card in enumerate(cards):
        width = CARD_W + (CARD_GAP if i < len(cards) - 1 else 0)
        for theme in CARD_THEMES:
            files[OUT / f'card-{card["slug"]}-{theme}.svg'] = card_svg(card, theme, lines, width)

    readme = README.read_text()
    readme = replace_block(readme, "production", panel_block(apps, total))
    readme = replace_block(readme, "cards", cards_block(cards))
    files[README] = readme

    changed = [path for path, content in files.items() if write(path, content)]
    # Rows and icons of apps that are no longer live go away with them.
    for stale in [*OUT.glob("app-*.svg"), *ICONS.glob("*.jpg")]:
        if stale not in files and stale not in icons:
            stale.unlink()
            changed.append(stale)
    return apps, total, changed


def main():
    config = json.loads(CONFIG.read_text())
    results = lookup([entry["id"] for entry in config["apps"]], config["country"])
    apps, total, changed = build(config, results)
    print(status(len(apps), total))
    for app in apps:
        print(f"  {app.name}  {app.version}  {app.date}")
    print(f"{len(changed)} file(s) changed")
    for path in changed:
        print(f"  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
