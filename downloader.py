"""Torrent download manager backed by libtorrent.

Runs a single libtorrent session in a background thread, polls each torrent
for progress, and persists status/progress back through the State object so
the .json file always reflects what the UI sees. Downloads resume across
restarts via fast-resume data.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

try:
    import libtorrent as lt  # type: ignore
    LIBTORRENT_AVAILABLE = True
    LIBTORRENT_ERROR = None
except Exception as _exc:  # pragma: no cover — only triggered without lib
    lt = None  # type: ignore
    LIBTORRENT_AVAILABLE = False
    LIBTORRENT_ERROR = str(_exc)


STATE_NAMES = {
    0: "queued",        # queued for checking
    1: "downloading",   # checking files
    2: "downloading",   # downloading metadata
    3: "downloading",
    4: "completed",     # finished
    5: "completed",     # seeding
    6: "queued",        # allocating
    7: "downloading",   # checking resume data
}


class Download:
    """One torrent transfer. Wraps a libtorrent torrent_handle."""

    def __init__(self, handle, magnet: str, save_path: str):
        self.handle = handle
        self.magnet = magnet
        self.save_path = save_path
        self.error: str | None = None

    @property
    def info_hash(self) -> str | None:
        try:
            return str(self.handle.info_hash())
        except Exception:
            return None

    @property
    def name(self) -> str:
        try:
            status = self.handle.status()
            if status.name:
                return status.name
        except Exception:
            pass
        return "(fetching metadata…)"

    def snapshot(self) -> dict:
        """Return a serialisable status dict for the UI / state file."""
        try:
            s = self.handle.status()
        except Exception as exc:
            return {
                "name": "(unavailable)",
                "status": "error",
                "progress": 0.0,
                "error": str(exc),
                "down_rate": 0,
                "up_rate": 0,
                "peers": 0,
                "seeds": 0,
                "leech": 0,
                "swarm_seeds": -1,
                "swarm_leech": -1,
                "eta": None,
            }
        status = STATE_NAMES.get(int(s.state), "downloading")
        if self.error:
            status = "error"
        progress = float(s.progress)
        connected_peers = int(s.num_peers)
        connected_seeds = int(s.num_seeds)
        connected_leech = max(0, connected_peers - connected_seeds)

        # Tracker-reported swarm totals — show them when we have them so the
        # user sees the real popularity, not just connected peers.
        swarm_seeds = int(getattr(s, "num_complete", -1) or -1)
        swarm_leech = int(getattr(s, "num_incomplete", -1) or -1)

        # ETA: bytes remaining / current download rate.
        eta = None
        try:
            remaining = int(s.total_wanted) - int(s.total_wanted_done)
        except Exception:
            remaining = 0
        if progress >= 1.0 or status == "completed":
            eta = 0
        elif remaining > 0 and s.download_rate > 0:
            eta = int(remaining / float(s.download_rate))

        return {
            "name": s.name or self.name,
            "status": status,
            "progress": progress,
            "error": self.error,
            "down_rate": int(s.download_rate),
            "up_rate": int(s.upload_rate),
            "peers": connected_peers,
            "seeds": connected_seeds,
            "leech": connected_leech,
            "swarm_seeds": swarm_seeds,
            "swarm_leech": swarm_leech,
            "eta": eta,
        }

    def pause(self) -> None:
        try:
            self.handle.pause()
        except Exception as exc:
            self.error = str(exc)

    def resume(self) -> None:
        try:
            self.handle.resume()
            self.error = None
        except Exception as exc:
            self.error = str(exc)


class DownloadManager:
    """Owns the libtorrent session and a worker thread that drives it."""

    def __init__(self, state):
        self.state = state
        self._session = None
        self._downloads: dict[str, Download] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if LIBTORRENT_AVAILABLE:
            self._init_session()
            self._restore_from_state()
            self._start_loop()

    # ── session lifecycle ────────────────────────────────────────────────
    def _init_session(self) -> None:
        assert lt is not None
        self._session = lt.session({
            "listen_interfaces": "0.0.0.0:6881,[::]:6881",
            "alert_mask": lt.alert.category_t.error_notification
                          | lt.alert.category_t.storage_notification
                          | lt.alert.category_t.status_notification,
        })
        # Public DHT routers — needed for magnet links to find peers.
        try:
            self._session.add_dht_router("router.bittorrent.com", 6881)
            self._session.add_dht_router("router.utorrent.com", 6881)
            self._session.add_dht_router("dht.transmissionbt.com", 6881)
            self._session.start_dht()
            self._session.start_lsd()
            self._session.start_upnp()
            self._session.start_natpmp()
        except Exception:
            pass

    def _restore_from_state(self) -> None:
        for entry in self.state.downloads():
            magnet = entry.get("magnet")
            if not magnet:
                continue
            if entry.get("status") == "completed":
                # Don't re-download; keep it in the list as a record.
                continue
            self.add_magnet(magnet, save_path=entry.get("save_path"), persist=False)

    def _start_loop(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._session:
            try:
                self._session.pause()
            except Exception:
                pass

    # ── public API ───────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return LIBTORRENT_AVAILABLE

    @property
    def unavailable_reason(self) -> str | None:
        return LIBTORRENT_ERROR

    def add_magnet(
        self,
        magnet: str,
        save_path: str | None = None,
        name_hint: str | None = None,
        persist: bool = True,
    ) -> Download | None:
        if not LIBTORRENT_AVAILABLE or self._session is None:
            return None
        save_dir = Path(save_path) if save_path else self.state.download_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        params = {
            "save_path": str(save_dir),
            "storage_mode": lt.storage_mode_t.storage_mode_sparse,
        }
        try:
            handle = lt.add_magnet_uri(self._session, magnet, params)
        except AttributeError:
            # Newer libtorrent versions
            params["url"] = magnet
            handle = self._session.add_torrent(params)
        except Exception as exc:
            if persist:
                self.state.add_download({
                    "info_hash": None,
                    "name": name_hint or magnet[:60],
                    "magnet": magnet,
                    "save_path": str(save_dir),
                    "status": "error",
                    "progress": 0.0,
                    "error": str(exc),
                })
            return None

        dl = Download(handle, magnet, str(save_dir))
        ih = dl.info_hash
        with self._lock:
            if ih:
                self._downloads[ih] = dl
        if persist:
            self.state.add_download({
                "info_hash": ih,
                "name": name_hint or dl.name,
                "magnet": magnet,
                "save_path": str(save_dir),
                "status": "queued",
                "progress": 0.0,
                "error": None,
            })
        return dl

    def list(self) -> list[dict]:
        """Merge live libtorrent state with persisted entries."""
        merged: list[dict] = []
        live_by_hash = {}
        with self._lock:
            for ih, dl in self._downloads.items():
                live_by_hash[ih] = dl.snapshot()

        for entry in self.state.downloads():
            ih = entry.get("info_hash")
            live = live_by_hash.get(ih)
            if live:
                merged.append({**entry, **live, "info_hash": ih})
            else:
                merged.append(entry)
        return merged

    def pause(self, info_hash: str) -> None:
        with self._lock:
            dl = self._downloads.get(info_hash)
        if dl:
            dl.pause()
            self.state.update_download(info_hash, status="paused")

    def resume(self, info_hash: str) -> None:
        with self._lock:
            dl = self._downloads.get(info_hash)
        if dl:
            dl.resume()
            self.state.update_download(info_hash, status="downloading", error=None)

    def remove(self, info_hash: str, delete_files: bool = False) -> None:
        with self._lock:
            dl = self._downloads.pop(info_hash, None)
        if dl and self._session is not None:
            try:
                flags = lt.session.delete_files if delete_files else 0
                self._session.remove_torrent(dl.handle, flags)
            except Exception:
                pass
        self.state.remove_download(info_hash)

    # ── background loop ─────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(1.0)

    def _tick(self) -> None:
        with self._lock:
            items = list(self._downloads.items())
        for ih, dl in items:
            snap = dl.snapshot()
            self.state.update_download(
                ih,
                name=snap["name"],
                status=snap["status"],
                progress=snap["progress"],
                error=snap["error"],
            )


def fmt_rate(bps: int) -> str:
    b = float(bps)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB/s"


def fmt_progress(p: float, width: int = 24) -> str:
    p = max(0.0, min(1.0, float(p)))
    filled = int(p * width)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {p * 100:5.1f}%"


def fmt_eta(seconds) -> str:
    if seconds is None:
        return "—"
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if s <= 0:
        return "done"
    if s >= 30 * 86400:
        return ">30d"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def fmt_swarm(connected: int, swarm: int) -> str:
    """Render '4 (123)' style — connected count plus tracker swarm size."""
    if connected is None:
        connected = 0
    if swarm is None or swarm < 0:
        return str(connected)
    return f"{connected} ({swarm})"
