"""Render screenshots of each pyxTorrent screen as PNGs.

Drives the actual screen objects with mocked input/clear so we capture clean
text frames, then paints each frame onto an image using Menlo (a macOS
monospace font) with a dark terminal-style background.

Usage: python render_screenshots.py
"""

from __future__ import annotations

import builtins
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import main as m
import search as search_mod
from state import State

OUT_DIR = Path(__file__).resolve().parent / "Screenshots"
OUT_DIR.mkdir(exist_ok=True)

FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
FONT_SIZE = 16
LINE_PAD = 4
BG = (24, 24, 28)
FG = (220, 220, 220)
DIM = (140, 140, 150)
CHROME = (60, 60, 70)


def render_text_to_png(text: str, out_path: Path, title: str = "") -> None:
    text = text.replace("\r", "").replace("\t", "    ")
    text = _strip_ansi(text)
    lines = text.splitlines() or [""]

    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    title_font = ImageFont.truetype(FONT_PATH, FONT_SIZE - 2)

    bbox = font.getbbox("M")
    char_w = bbox[2] - bbox[0]
    line_h = (bbox[3] - bbox[1]) + LINE_PAD

    max_cols = max((len(l) for l in lines), default=80)
    max_cols = max(max_cols, 84)

    margin_x = 24
    margin_top = 44 if title else 18
    margin_bottom = 18

    img_w = margin_x * 2 + char_w * max_cols
    img_h = margin_top + margin_bottom + line_h * len(lines)

    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    if title:
        # Window-chrome bar
        draw.rectangle([0, 0, img_w, 32], fill=CHROME)
        for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            draw.ellipse([14 + i * 18, 10, 26 + i * 18, 22], fill=color)
        draw.text(
            (img_w // 2 - len(title) * 4, 8),
            title,
            font=title_font,
            fill=DIM,
        )

    y = margin_top
    for line in lines:
        draw.text((margin_x, y), line, font=font, fill=FG)
        y += line_h

    img.save(out_path)
    print(f"  wrote {out_path.relative_to(Path.cwd())} ({img_w}x{img_h})")


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


# ── App fakes ─────────────────────────────────────────────────────────


class _RecordingApp:
    """Stand-in for main.App with whatever attrs each screen needs."""

    def __init__(self, downloader=None, engines=None, state=None, history=None):
        self.state = state or State()
        if history:
            for h in history:
                self.state.push_history(h)
        self.engines = engines if engines is not None else []
        self.downloader = downloader or _NullDownloader()
        self.stack = []


class _NullDownloader:
    available = False
    unavailable_reason = "(simulated — libtorrent not loaded for this render)"

    def list(self):
        return []


class _MockDownloader:
    available = True
    unavailable_reason = None

    def __init__(self, items):
        self._items = items

    def list(self):
        return list(self._items)


def _capture(screen, app, input_value: str = "b") -> str:
    """Render a screen, returning everything it printed.

    Patches clear() (no-op) and input() (returns ``input_value``) so the
    screen draws its frame and then 'returns' from prompt() instantly.
    """
    real_clear = m.clear
    real_input = builtins.input
    m.clear = lambda: None
    builtins.input = lambda *a, **k: input_value
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            screen.render(app)
    finally:
        m.clear = real_clear
        builtins.input = real_input
    return buf.getvalue()


# ── individual scenes ────────────────────────────────────────────────


def scene_main_menu(out: Path) -> None:
    state = State()
    engines = search_mod.load_engines()
    items = [{
        "info_hash": "a" * 40, "name": "Sintel (2010) 1080p",
        "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
        "save_path": str(state.download_dir),
        "status": "downloading", "progress": 0.42,
        "down_rate": 2_500_000, "up_rate": 130_000,
        "peers": 24, "seeds": 9, "leech": 15,
        "swarm_seeds": 412, "swarm_leech": 1903,
        "eta": 323, "error": None,
    }]
    app = _RecordingApp(downloader=_MockDownloader(items), engines=engines, state=state)
    text = _capture(m.MainMenu(), app)
    render_text_to_png(text, out, title="pyxTorrent ·main menu")


def scene_search_input(out: Path) -> None:
    history = [
        "ubuntu 24.04",
        "the expanse s06",
        "sintel",
        "-r 7d \"big buck bunny\"",
    ]
    state = State()
    # Wipe existing history so the screenshot is deterministic.
    state._data["history"] = []
    state.save()
    for h in history:
        state.push_history(h)
    app = _RecordingApp(state=state, engines=search_mod.load_engines())
    text = _capture(m.SearchInput(), app)
    render_text_to_png(text, out, title="pyxTorrent ·search")


def scene_search_results(out: Path) -> None:
    state = State()
    engines = search_mod.load_engines()
    # Build representative result fixtures so the layout is stable.
    results = [
        {"name": "Sintel (2010) 1080p BluRay x264-AMIABLE", "seeds": 412, "leech": 23,
         "size": "2147483648 B", "engine_url": "https://thepiratebay.org",
         "link": "magnet:?xt=urn:btih:" + "a" * 40},
        {"name": "Sintel.2010.4K.UHD.HDR10.HEVC-GROUP", "seeds": 188, "leech": 12,
         "size": "16106127360 B", "engine_url": "https://solidtorrents.to",
         "link": "magnet:?xt=urn:btih:" + "b" * 40},
        {"name": "Big Buck Bunny - 1080p (2008)", "seeds": 96, "leech": 4,
         "size": "734003200 B", "engine_url": "https://torrents-csv.com",
         "link": "magnet:?xt=urn:btih:" + "c" * 40},
        {"name": "Tears of Steel 2012 1080p WEB-DL", "seeds": 41, "leech": 1,
         "size": "1288490188 B", "engine_url": "https://eztv.re",
         "link": "magnet:?xt=urn:btih:" + "d" * 40},
        {"name": "Cosmos Laundromat 2015 4K HDR", "seeds": 12, "leech": 0,
         "size": "5368709120 B", "engine_url": "https://torlock.com",
         "link": "magnet:?xt=urn:btih:" + "e" * 40},
        {"name": "Caminandes Llamigos 2016 1080p", "seeds": 7, "leech": 0,
         "size": "104857600 B", "engine_url": "https://limetorrents.lol",
         "link": "magnet:?xt=urn:btih:" + "f" * 40},
    ]
    screen = m.ResultsScreen(query='"sintel"', results=results, sort_by_date=False)
    app = _RecordingApp(state=state, engines=engines)
    text = _capture(screen, app)
    render_text_to_png(text, out, title="pyxTorrent ·search results")


def scene_torrent_detail(out: Path) -> None:
    state = State()
    result = {
        "name": "Sintel (2010) 1080p BluRay x264-AMIABLE",
        "seeds": 412, "leech": 23,
        "size": "2147483648 B",
        "engine_url": "https://thepiratebay.org",
        "desc_link": "https://thepiratebay.org/description.php?id=12345",
        "link": "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Sintel.2010.1080p&tr=udp://tracker.openbittorrent.com:6969",
        "pub_date": 1714032000,
    }
    items = []
    app = _RecordingApp(
        state=state,
        engines=[],
        downloader=_MockDownloader(items),
    )
    text = _capture(m.TorrentDetail(result), app)
    render_text_to_png(text, out, title="pyxTorrent ·torrent detail")


def scene_downloads(out: Path) -> None:
    items = [
        {
            "info_hash": "a" * 40, "name": "Sintel (2010) 1080p BluRay x264",
            "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
            "save_path": "C:\\Users\\you\\Downloads",
            "status": "downloading", "progress": 0.42,
            "down_rate": 2_500_000, "up_rate": 130_000,
            "peers": 24, "seeds": 9, "leech": 15,
            "swarm_seeds": 412, "swarm_leech": 23,
            "eta": 5 * 60 + 23, "error": None,
        },
        {
            "info_hash": "b" * 40, "name": "Big Buck Bunny - 1080p (2008)",
            "magnet": "", "save_path": "C:\\Users\\you\\Downloads",
            "status": "queued", "progress": 0.0,
            "down_rate": 0, "up_rate": 0,
            "peers": 0, "seeds": 0, "leech": 0,
            "swarm_seeds": 96, "swarm_leech": 4,
            "eta": None, "error": None,
        },
        {
            "info_hash": "c" * 40, "name": "Tears of Steel 2012 1080p WEB-DL",
            "magnet": "", "save_path": "C:\\Users\\you\\Downloads",
            "status": "completed", "progress": 1.0,
            "down_rate": 0, "up_rate": 50_000,
            "peers": 14, "seeds": 14, "leech": 0,
            "swarm_seeds": 41, "swarm_leech": 1,
            "eta": 0, "error": None,
        },
        {
            "info_hash": "d" * 40, "name": "Cosmos Laundromat 2015 4K HDR",
            "magnet": "", "save_path": "C:\\Users\\you\\Downloads",
            "status": "paused", "progress": 0.18,
            "down_rate": 0, "up_rate": 0,
            "peers": 0, "seeds": 0, "leech": 0,
            "swarm_seeds": 12, "swarm_leech": 0,
            "eta": None, "error": None,
        },
    ]
    state = State()
    app = _RecordingApp(state=state, downloader=_MockDownloader(items))
    text = _capture(m.DownloadsScreen(), app)
    render_text_to_png(text, out, title="pyxTorrent ·downloads")


def scene_download_detail(out: Path) -> None:
    item = {
        "info_hash": "a" * 40, "name": "Sintel (2010) 1080p BluRay x264-AMIABLE",
        "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
        "save_path": "C:\\Users\\you\\Downloads",
        "status": "downloading", "progress": 0.42,
        "down_rate": 2_500_000, "up_rate": 130_000,
        "peers": 24, "seeds": 9, "leech": 15,
        "swarm_seeds": 412, "swarm_leech": 23,
        "eta": 5 * 60 + 23, "error": None,
    }
    state = State()
    app = _RecordingApp(state=state, downloader=_MockDownloader([item]))
    text = _capture(m.DownloadDetail(item), app)
    render_text_to_png(text, out, title="pyxTorrent ·download detail")


def scene_settings(out: Path) -> None:
    state = State()
    app = _RecordingApp(state=state, engines=search_mod.load_engines())
    text = _capture(m.SettingsScreen(), app)
    render_text_to_png(text, out, title="pyxTorrent ·settings")


def main() -> int:
    print("Rendering screenshots…")
    scene_main_menu(OUT_DIR / "01_main_menu.png")
    scene_search_input(OUT_DIR / "02_search.png")
    scene_search_results(OUT_DIR / "03_results.png")
    scene_torrent_detail(OUT_DIR / "04_torrent_detail.png")
    scene_downloads(OUT_DIR / "05_downloads.png")
    scene_download_detail(OUT_DIR / "06_download_detail.png")
    scene_settings(OUT_DIR / "07_settings.png")
    print(f"\nDone — {len(list(OUT_DIR.glob('*.png')))} screenshots in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
