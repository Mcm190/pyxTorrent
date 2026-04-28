"""Persistent app state stored in pyxtorrent_state.json next to the program.

Schema (versioned so we can migrate later):
{
  "version": 1,
  "settings": {"download_dir": "<abs path>", "max_results": 30},
  "history":  ["query1", "query2", ...]   # most-recent first
  "downloads": [
     {
       "info_hash": "...",   # 40-char hex if known, else None
       "name": "...",
       "magnet": "magnet:?...",
       "save_path": "/abs/path",
       "added_at": <unix ts>,
       "status": "queued" | "downloading" | "paused" | "completed" | "error",
       "progress": 0.0..1.0,
       "error": "..." | None
     },
     ...
  ]
}
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

STATE_VERSION = 1
STATE_FILENAME = "pyxtorrent_state.json"


def program_dir() -> Path:
    """Folder the program runs from — used for state file and default download dir.

    For a PyInstaller --onefile bundle, sys._MEIPASS points at a temp extraction
    dir; we want the dir of the actual .exe instead. sys.executable is correct
    when frozen, otherwise fall back to the source location.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def state_path() -> Path:
    return program_dir() / STATE_FILENAME


def default_state() -> dict:
    return {
        "version": STATE_VERSION,
        "settings": {
            "download_dir": str(program_dir()),
            "max_results": 30,
        },
        "history": [],
        "downloads": [],
    }


class State:
    """Thread-safe wrapper around the JSON state file."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data = default_state()
        self.load()

    # ── persistence ──────────────────────────────────────────────────────
    def load(self) -> None:
        path = state_path()
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        with self._lock:
            merged = default_state()
            merged.update({k: v for k, v in raw.items() if k in merged})
            # Make sure settings has all expected keys
            base_settings = default_state()["settings"]
            base_settings.update(raw.get("settings", {}) or {})
            merged["settings"] = base_settings
            self._data = merged

    def save(self) -> None:
        path = state_path()
        with self._lock:
            payload = json.dumps(self._data, indent=2, ensure_ascii=False)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
        except OSError:
            # Last-ditch: try a non-atomic write so we don't lose the data.
            try:
                with path.open("w", encoding="utf-8") as f:
                    f.write(payload)
            except OSError:
                pass

    # ── settings ─────────────────────────────────────────────────────────
    def get_setting(self, key: str, default=None):
        with self._lock:
            return self._data["settings"].get(key, default)

    def set_setting(self, key: str, value) -> None:
        with self._lock:
            self._data["settings"][key] = value
        self.save()

    @property
    def download_dir(self) -> Path:
        return Path(self.get_setting("download_dir", str(program_dir())))

    @property
    def max_results(self) -> int:
        try:
            return int(self.get_setting("max_results", 30))
        except (TypeError, ValueError):
            return 30

    # ── history ──────────────────────────────────────────────────────────
    def push_history(self, query: str, limit: int = 25) -> None:
        if not query:
            return
        with self._lock:
            hist = [h for h in self._data["history"] if h != query]
            hist.insert(0, query)
            self._data["history"] = hist[:limit]
        self.save()

    def history(self) -> list[str]:
        with self._lock:
            return list(self._data["history"])

    # ── downloads ────────────────────────────────────────────────────────
    def downloads(self) -> list[dict]:
        with self._lock:
            return [dict(d) for d in self._data["downloads"]]

    def add_download(self, entry: dict) -> dict:
        entry = dict(entry)
        entry.setdefault("added_at", int(time.time()))
        entry.setdefault("status", "queued")
        entry.setdefault("progress", 0.0)
        entry.setdefault("error", None)
        with self._lock:
            # Dedupe by info_hash if present
            if entry.get("info_hash"):
                self._data["downloads"] = [
                    d for d in self._data["downloads"]
                    if d.get("info_hash") != entry["info_hash"]
                ]
            self._data["downloads"].append(entry)
        self.save()
        return entry

    def update_download(self, info_hash: str, **fields) -> None:
        if not info_hash:
            return
        with self._lock:
            for d in self._data["downloads"]:
                if d.get("info_hash") == info_hash:
                    d.update(fields)
                    break
        self.save()

    def remove_download(self, info_hash: str) -> None:
        if not info_hash:
            return
        with self._lock:
            self._data["downloads"] = [
                d for d in self._data["downloads"]
                if d.get("info_hash") != info_hash
            ]
        self.save()
