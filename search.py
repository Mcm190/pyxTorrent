"""Torrent search across qBittorrent-compatible engine plugins.

Logic adapted from qbitsearch/qsearch.py but reorganised as a library so the
TUI can drive it. Engines remain plug-compatible with the qbittorrent
search-plugin interface (each module defines a class with the same name as
the file, exposing .search(query, category) and calling
novaprinter.prettyPrinter for each hit).
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


def _engines_dir() -> Path:
    """Locate the bundled engines/ folder, whether running from source or
    a PyInstaller --onefile build (which extracts data to sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / "engines"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent / "engines"


ENGINES_DIR = _engines_dir()
DEFAULT_TIMEOUT = 30
RECENT_TIMEOUT = 60

_SKIP_MODULES = {"__init__", "helpers", "novaprinter", "jackett"}


def load_engines() -> list[tuple[str, object]]:
    """Import every plugin module in engines/ and instantiate its engine class."""
    engines_dir = str(ENGINES_DIR)
    if engines_dir not in sys.path:
        sys.path.insert(0, engines_dir)

    engines: list[tuple[str, object]] = []
    if not ENGINES_DIR.is_dir():
        return engines

    for path in sorted(ENGINES_DIR.glob("*.py")):
        if path.stem in _SKIP_MODULES:
            continue
        try:
            mod = importlib.import_module(path.stem)
            cls = getattr(mod, path.stem, None)
            if cls and callable(getattr(cls, "search", None)):
                engines.append((path.stem, cls()))
        except Exception as exc:
            print(f"[!] Could not load engine {path.stem}: {exc}", file=sys.stderr)
    return engines


def run_search(
    engines: list[tuple[str, object]],
    query: str,
    timeout: int = DEFAULT_TIMEOUT,
    on_progress=None,
) -> tuple[list[dict], dict[str, str]]:
    """Run all engine searches concurrently. Returns (results, status_per_engine).

    on_progress(name, state, count) — optional callback fired whenever an
    engine finishes (or times out). Lets the UI stream completion in realtime.
    """
    import novaprinter  # noqa: WPS433 — engine-module dependency

    novaprinter.results.clear()
    counts: dict[str, int] = {}
    status: dict[str, str] = {}
    counts_lock = threading.Lock()

    def worker(name: str, engine: object) -> None:
        encoded = urllib.parse.quote_plus(query)
        before = len(novaprinter.results)
        try:
            engine.search(encoded, "all")
            after = len(novaprinter.results)
            with counts_lock:
                counts[name] = after - before
            status[name] = "done"
        except Exception as exc:
            status[name] = f"error: {exc}"

    threads: list[tuple[str, threading.Thread]] = []
    for name, engine in engines:
        status[name] = "searching"
        t = threading.Thread(target=worker, args=(name, engine), daemon=True)
        threads.append((name, t))
        t.start()

    notified: set[str] = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for name in list(status):
            if status[name] != "searching" and name not in notified:
                notified.add(name)
                if on_progress:
                    on_progress(name, status[name], counts.get(name, 0))
        if all(status[n] != "searching" for n, _ in threads):
            break
        time.sleep(0.25)

    for name, _ in threads:
        if status.get(name) == "searching":
            status[name] = f"timeout (>{timeout}s)"
        if name not in notified:
            notified.add(name)
            if on_progress:
                on_progress(name, status[name], counts.get(name, 0))

    # Copy out before the next search clears the shared list.
    return list(novaprinter.results), status


def info_hash(link: str) -> str | None:
    """Pull the BTIH from a magnet link. Returns None if not a magnet."""
    m = re.search(r"urn:btih:([a-fA-F0-9]{32,40})", link or "", re.I)
    return m.group(1).lower() if m else None


def deduplicate(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in results:
        ih = info_hash(r.get("link", ""))
        key = ih if ih else r.get("link", "")
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def human_size(raw: str) -> str:
    try:
        b = float(str(raw).split()[0])
    except (ValueError, IndexError):
        return raw if raw and raw != "-1" else "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def parse_duration(s: str) -> int:
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    s = s.strip().lower()
    if not s:
        raise ValueError("empty duration")
    suffix = s[-1]
    if suffix not in units:
        raise ValueError(f"unknown unit '{suffix}' — use m, h, d, or w")
    try:
        value = int(s[:-1])
    except ValueError as exc:
        raise ValueError(f"invalid duration '{s}'") from exc
    if value <= 0:
        raise ValueError("duration must be positive")
    return value * units[suffix]


def filter_recent(results: list[dict], since_seconds: int) -> list[dict]:
    cutoff = int(datetime.now(timezone.utc).timestamp()) - since_seconds
    return [
        r for r in results
        if isinstance(r.get("pub_date"), int) and r["pub_date"] > cutoff
    ]


def sort_results(results: list[dict], by_date: bool = False) -> list[dict]:
    results = deduplicate(results)
    if by_date:
        dated = [r for r in results if isinstance(r.get("pub_date"), int) and r["pub_date"] > 0]
        undated = [r for r in results if not (isinstance(r.get("pub_date"), int) and r["pub_date"] > 0)]
        return sorted(dated, key=lambda r: r["pub_date"], reverse=True) + undated
    valid = [r for r in results if r.get("seeds", -1) >= 0]
    no_seed = [r for r in results if r.get("seeds", -1) < 0]
    return sorted(valid, key=lambda r: r["seeds"], reverse=True) + no_seed


def build_query(terms: list[str]) -> str:
    """Quote each term so multi-word searches stay exact."""
    return " ".join(f'"{t.replace(chr(34), chr(92) + chr(34))}"' for t in terms)
