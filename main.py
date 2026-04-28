"""pyxTorrent — terminal torrent search & download client.

Navigation model: a stack of Screen objects. Each screen renders itself,
asks the user for input, then returns one of:
  - ("push", NewScreen)     — go deeper (back will return here)
  - ("pop", None)           — go back one screen
  - ("replace", NewScreen)  — swap current screen, no back
  - ("quit", None)          — exit the app
  - ("noop", None)          — re-render (useful after an inline action)

Type 'b' or 'back' from any screen to pop. Type 'q' or 'quit' to exit.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import search
from downloader import DownloadManager, fmt_eta, fmt_progress, fmt_rate, fmt_swarm
from state import State


# ── tiny UI helpers ─────────────────────────────────────────────────────

def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def width() -> int:
    try:
        return shutil.get_terminal_size((80, 24)).columns
    except Exception:
        return 80


def hr(char: str = "─") -> str:
    return char * max(40, min(width(), 100))


def header(title: str, subtitle: str = "") -> None:
    bar = hr()
    print(bar)
    print(f"  pyxTorrent · {title}")
    if subtitle:
        print(f"  {subtitle}")
    print(bar)


def footer_hint(extra: str = "") -> None:
    print(hr())
    base = "[b] back   [q] quit"
    print(f"  {base}   {extra}".rstrip())


def prompt(msg: str) -> str:
    try:
        return input(f"\n{msg} ").strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def truncate(s: str, n: int) -> str:
    s = str(s)
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def parse_int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


# ── screens ────────────────────────────────────────────────────────────

class Screen:
    title = "screen"

    def render(self, app: "App"):
        """Draw the screen and return (action, payload)."""
        raise NotImplementedError


class MainMenu(Screen):
    title = "main menu"

    def render(self, app):
        clear()
        header("Main Menu")
        engines = app.engines
        print()
        print(f"  Engines loaded: {len(engines)}")
        print(f"  Download dir:   {app.state.download_dir}")
        if not app.downloader.available:
            print(f"  Downloader:     UNAVAILABLE — {app.downloader.unavailable_reason}")
        else:
            print(f"  Active downloads: {sum(1 for d in app.downloader.list() if d.get('status') in ('downloading', 'queued'))}")
        print()
        print("  1) Search torrents")
        print("  2) Downloads")
        print("  3) Add magnet link")
        print("  4) Settings")
        print("  5) Recent searches")
        print("  q) Quit")
        footer_hint()
        choice = prompt(">").lower()
        if choice == "1":
            return ("push", SearchInput())
        if choice == "2":
            return ("push", DownloadsScreen())
        if choice == "3":
            return ("push", AddMagnetScreen())
        if choice == "4":
            return ("push", SettingsScreen())
        if choice == "5":
            return ("push", HistoryScreen())
        if choice in ("q", "quit", "exit"):
            return ("quit", None)
        return ("noop", None)


class SearchInput(Screen):
    title = "search"

    def render(self, app):
        clear()
        header("Search", "Enter terms separated by spaces. Wrap multi-word phrases in quotes.")
        print()
        recent = app.state.history()[:5]
        if recent:
            print("  Recent:")
            for i, q in enumerate(recent, 1):
                print(f"    {i}) {truncate(q, 70)}")
            print()
        print("  Tip: prefix the line with -r 7d  to filter to last 7 days.")
        print("  Tip: type a number above to repeat that recent search.")
        footer_hint()
        line = prompt("Search>")
        if not line or line.lower() in ("b", "back"):
            return ("pop", None)
        if line.lower() in ("q", "quit"):
            return ("quit", None)

        # Repeat from history
        idx = parse_int(line)
        if idx is not None and 1 <= idx <= len(recent):
            line = recent[idx - 1]

        recent_seconds: int | None = None
        terms: list[str] = []
        tokens = _shlex_split(line)
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-r", "--recent") and i + 1 < len(tokens):
                try:
                    recent_seconds = search.parse_duration(tokens[i + 1])
                except ValueError as exc:
                    print(f"  bad duration: {exc}")
                    prompt("(enter to continue)")
                    return ("noop", None)
                i += 2
                continue
            terms.append(tok)
            i += 1

        if not terms:
            return ("noop", None)

        query = search.build_query(terms)
        app.state.push_history(line)
        return ("push", SearchRunning(query=query, recent_seconds=recent_seconds))


class SearchRunning(Screen):
    """Runs the search inline (blocking with live engine completion log) and
    immediately replaces itself with a results screen."""
    title = "searching"

    def __init__(self, query: str, recent_seconds: int | None):
        self.query = query
        self.recent_seconds = recent_seconds

    def render(self, app):
        clear()
        header("Searching", self.query)
        engine_names = {n: getattr(e, "name", n) for n, e in app.engines}
        print()
        print(f"  Querying {len(app.engines)} engines…\n")

        def on_progress(name, state, count):
            label = f"{count} results" if state == "done" else state
            icon = "✓" if state == "done" else "✗"
            print(f"    [{icon}] {engine_names.get(name, name):20s} {label}")

        timeout = search.RECENT_TIMEOUT if self.recent_seconds else search.DEFAULT_TIMEOUT
        results, _status = search.run_search(
            app.engines, self.query, timeout=timeout, on_progress=on_progress
        )

        sort_by_date = False
        if self.recent_seconds:
            filtered = search.filter_recent(results, self.recent_seconds)
            if filtered:
                results = filtered
            sort_by_date = True

        results = search.sort_results(results, by_date=sort_by_date)
        print(f"\n  {len(results)} unique results.")
        time.sleep(0.4)
        return ("replace", ResultsScreen(query=self.query, results=results, sort_by_date=sort_by_date))


class ResultsScreen(Screen):
    title = "results"

    def __init__(self, query: str, results: list[dict], sort_by_date: bool):
        self.query = query
        self.results = results
        self.sort_by_date = sort_by_date
        self.page = 0

    def render(self, app):
        clear()
        per_page = max(5, min(app.state.max_results, 50))
        total = len(self.results)
        pages = max(1, (total + per_page - 1) // per_page)
        self.page = max(0, min(self.page, pages - 1))
        start = self.page * per_page
        end = min(start + per_page, total)

        sort_label = "newest first" if self.sort_by_date else "most seeders"
        header(f"Results · {self.query}",
               f"{total} results · sorted by {sort_label} · page {self.page + 1}/{pages}")
        print()
        if not self.results:
            print("  No results.")
            footer_hint("[s] new search")
            cmd = prompt(">").lower()
            if cmd == "s":
                return ("replace", SearchInput())
            return ("pop", None)

        cols = width()
        name_col = max(30, cols - 40)
        print(f"  {'#':>3}  {'name':<{name_col}}  {'seeds':>6}  {'size':>10}  src")
        print(f"  {hr('·')[:3 + 2 + name_col + 2 + 6 + 2 + 10 + 2 + 12]}")
        for i in range(start, end):
            r = self.results[i]
            seeds = r.get("seeds", -1)
            seed_str = str(seeds) if seeds >= 0 else "?"
            size = search.human_size(r.get("size", "-1"))
            src = (r.get("engine_url", "") or "").split("//")[-1].split("/")[0][:12]
            print(f"  {i + 1:>3}  {truncate(r.get('name',''), name_col):<{name_col}}  {seed_str:>6}  {size:>10}  {src}")

        nav_extra = "[n] next page   [p] prev page   [s] new search   [#] open result"
        footer_hint(nav_extra)
        cmd = prompt(">").lower()
        if cmd in ("n", "next"):
            if self.page < pages - 1:
                self.page += 1
            return ("noop", None)
        if cmd in ("p", "prev"):
            if self.page > 0:
                self.page -= 1
            return ("noop", None)
        if cmd in ("s", "search"):
            return ("replace", SearchInput())
        if cmd in ("b", "back"):
            return ("pop", None)
        if cmd in ("q", "quit"):
            return ("quit", None)
        idx = parse_int(cmd)
        if idx is not None and 1 <= idx <= total:
            return ("push", TorrentDetail(self.results[idx - 1]))
        return ("noop", None)


class TorrentDetail(Screen):
    title = "torrent"

    def __init__(self, result: dict):
        self.r = result

    def render(self, app):
        clear()
        header("Torrent", truncate(self.r.get("name", ""), 80))
        r = self.r
        ih = search.info_hash(r.get("link", "") or "")
        seeds = r.get("seeds", -1)
        leech = r.get("leech", -1)
        pub = r.get("pub_date")
        pub_str = ""
        if isinstance(pub, int) and pub > 0:
            pub_str = datetime.fromtimestamp(pub, tz=timezone.utc).strftime("%Y-%m-%d")

        print()
        print(f"  Name:       {r.get('name', '')}")
        print(f"  Size:       {search.human_size(r.get('size', '-1'))}")
        print(f"  Seeders:    {seeds if seeds >= 0 else '?'}")
        print(f"  Leechers:   {leech if leech >= 0 else '?'}")
        if pub_str:
            print(f"  Published:  {pub_str}")
        if r.get("desc_link"):
            print(f"  Info page:  {r['desc_link']}")
        print(f"  Source:     {r.get('engine_url', '')}")
        if ih:
            print(f"  Info hash:  {ih}")
        link = r.get("link", "")
        is_magnet = link.startswith("magnet:")
        print(f"  Link type:  {'magnet' if is_magnet else 'torrent URL'}")
        print()
        print(f"  Will save to: {app.state.download_dir}")

        actions = ["[d] download"]
        if not is_magnet:
            actions.append("(only magnets supported)")
        actions += ["[c] copy magnet/url to clipboard", "[v] view full link"]
        if not app.downloader.available:
            actions = ["[v] view full link", "(downloader unavailable: install libtorrent)"]
        footer_hint("   ".join(actions))
        cmd = prompt(">").lower()
        if cmd in ("b", "back"):
            return ("pop", None)
        if cmd in ("q", "quit"):
            return ("quit", None)
        if cmd == "v":
            print()
            print(link)
            prompt("(enter to continue)")
            return ("noop", None)
        if cmd == "c":
            _copy_to_clipboard(link)
            return ("noop", None)
        if cmd == "d":
            if not app.downloader.available:
                print(f"\n  libtorrent not installed: {app.downloader.unavailable_reason}")
                prompt("(enter to continue)")
                return ("noop", None)
            if not is_magnet:
                print("\n  Only magnet links can be downloaded right now.")
                prompt("(enter to continue)")
                return ("noop", None)
            dl = app.downloader.add_magnet(
                link,
                save_path=str(app.state.download_dir),
                name_hint=r.get("name"),
            )
            if dl is None:
                print("\n  Failed to add torrent.")
                prompt("(enter to continue)")
                return ("noop", None)
            print(f"\n  Added. Saving to: {app.state.download_dir}")
            prompt("(enter to view downloads)")
            return ("replace", DownloadsScreen())
        return ("noop", None)


class DownloadsScreen(Screen):
    title = "downloads"

    def render(self, app):
        clear()
        header("Downloads", str(app.state.download_dir))
        if not app.downloader.available:
            print()
            print(f"  libtorrent unavailable: {app.downloader.unavailable_reason}")
            print("  Install it (pip install libtorrent) and restart to enable downloads.")
            footer_hint()
            cmd = prompt(">").lower()
            if cmd in ("b", "back"):
                return ("pop", None)
            if cmd in ("q", "quit"):
                return ("quit", None)
            return ("noop", None)

        items = app.downloader.list()
        print()
        if not items:
            print("  No downloads yet. Search and pick a torrent.")
        else:
            cols = width()
            # Reserve room for status / progress / S/L / ETA / rate columns.
            name_col = max(20, cols - 78)
            print(
                f"  {'#':>3}  {'name':<{name_col}}  "
                f"{'status':<10}  {'progress':<28}  "
                f"{'S/L':>9}  {'ETA':>10}  rate"
            )
            for i, d in enumerate(items, 1):
                name = truncate(d.get("name", ""), name_col)
                status = d.get("status", "?")
                bar = fmt_progress(d.get("progress", 0.0), width=20)
                seeds = d.get("seeds")
                leech = d.get("leech")
                sl = (
                    f"{seeds if seeds is not None else '?'}/"
                    f"{leech if leech is not None else '?'}"
                )
                eta = fmt_eta(d.get("eta")) if d.get("eta") is not None or d.get("status") == "completed" else "—"
                rate = fmt_rate(d.get("down_rate", 0)) if d.get("down_rate") else "—"
                print(
                    f"  {i:>3}  {name:<{name_col}}  {status:<10}  {bar}  "
                    f"{sl:>9}  {eta:>10}  {rate}"
                )
                if d.get("error"):
                    print(f"        ! {d['error']}")
        footer_hint("[r] refresh   [#] manage   [a] add magnet manually")
        cmd = prompt(">").lower()
        if cmd in ("b", "back"):
            return ("pop", None)
        if cmd in ("q", "quit"):
            return ("quit", None)
        if cmd in ("r", "refresh", ""):
            return ("noop", None)
        if cmd == "a":
            return ("push", AddMagnetScreen())
        idx = parse_int(cmd)
        if idx is not None and 1 <= idx <= len(items):
            return ("push", DownloadDetail(items[idx - 1]))
        return ("noop", None)


class DownloadDetail(Screen):
    title = "download"

    def __init__(self, entry: dict):
        self.entry = entry

    def render(self, app):
        clear()
        header("Download", truncate(self.entry.get("name", ""), 80))
        # Refresh from manager
        ih = self.entry.get("info_hash")
        live = next((d for d in app.downloader.list() if d.get("info_hash") == ih), self.entry)
        print()
        print(f"  Name:     {live.get('name', '')}")
        print(f"  Status:   {live.get('status', '?')}")
        print(f"  Progress: {fmt_progress(live.get('progress', 0.0))}")
        eta_label = (
            "done" if (live.get("status") == "completed" or (live.get("progress") or 0) >= 1.0)
            else fmt_eta(live.get("eta"))
        )
        print(f"  ETA:      {eta_label}")
        if live.get("down_rate") is not None:
            print(f"  Down:     {fmt_rate(live.get('down_rate', 0))}   Up: {fmt_rate(live.get('up_rate', 0))}")
        seeds = live.get("seeds")
        leech = live.get("leech")
        swarm_s = live.get("swarm_seeds", -1)
        swarm_l = live.get("swarm_leech", -1)
        if seeds is not None or leech is not None:
            print(f"  Seeders:  {fmt_swarm(seeds or 0, swarm_s if swarm_s is not None else -1)}   "
                  f"Leechers: {fmt_swarm(leech or 0, swarm_l if swarm_l is not None else -1)}")
            print(f"            (connected · total in swarm from tracker)")
        print(f"  Saving:   {live.get('save_path', '')}")
        if live.get("error"):
            print(f"  Error:    {live['error']}")
        if ih:
            print(f"  Hash:     {ih}")

        actions = "[p] pause/resume   [r] refresh   [x] remove   [X] remove + delete files"
        footer_hint(actions)
        cmd = prompt(">").lower()
        if cmd in ("b", "back"):
            return ("pop", None)
        if cmd in ("q", "quit"):
            return ("quit", None)
        if cmd in ("r", "refresh", ""):
            return ("noop", None)
        if cmd == "p":
            if not ih:
                return ("noop", None)
            if live.get("status") == "paused":
                app.downloader.resume(ih)
            else:
                app.downloader.pause(ih)
            return ("noop", None)
        if cmd == "x":
            if ih:
                app.downloader.remove(ih, delete_files=False)
            return ("pop", None)
        if cmd == "X":
            if ih:
                confirm = prompt("Delete files from disk too? type DELETE to confirm:")
                if confirm == "DELETE":
                    app.downloader.remove(ih, delete_files=True)
                    return ("pop", None)
            return ("noop", None)
        return ("noop", None)


class AddMagnetScreen(Screen):
    """Add a torrent by magnet link (or raw 40-char info hash)."""

    title = "add magnet"

    def render(self, app):
        clear()
        header("Add magnet", "use this when a torrent isn't in the search results")
        print()
        print("  Paste one of:")
        print("    • a full magnet link  (magnet:?xt=urn:btih:…)")
        print("    • a 40-character info hash  (we'll build the magnet)")
        print()
        print(f"  Will save to: {app.state.download_dir}")
        if not app.downloader.available:
            print()
            print(f"  ! libtorrent not installed — {app.downloader.unavailable_reason}")
        footer_hint()
        line = prompt("magnet:?>").strip()
        if not line or line.lower() in ("b", "back"):
            return ("pop", None)
        if line.lower() in ("q", "quit"):
            return ("quit", None)

        magnet = _coerce_magnet(line)
        if not magnet:
            print("\n  That doesn't look like a magnet link or 40-char info hash.")
            prompt("(enter to continue)")
            return ("noop", None)
        if not app.downloader.available:
            print(f"\n  libtorrent not installed: {app.downloader.unavailable_reason}")
            prompt("(enter to continue)")
            return ("noop", None)
        dl = app.downloader.add_magnet(magnet, save_path=str(app.state.download_dir))
        if dl is None:
            print("\n  Failed to add torrent.")
            prompt("(enter to continue)")
            return ("noop", None)
        print("\n  Added — fetching metadata, then downloading.")
        prompt("(enter to view downloads)")
        return ("replace", DownloadsScreen())


class SettingsScreen(Screen):
    title = "settings"

    def render(self, app):
        clear()
        header("Settings")
        print()
        print(f"  1) Download dir   {app.state.download_dir}")
        print(f"  2) Max results    {app.state.max_results}")
        print(f"  3) State file     {Path(__file__).resolve().parent / 'pyxtorrent_state.json'}")
        print(f"  4) Engines loaded {len(app.engines)}")
        print()
        print("  Pick a number to edit, or [b] back.")
        footer_hint()
        cmd = prompt(">").lower()
        if cmd in ("b", "back"):
            return ("pop", None)
        if cmd in ("q", "quit"):
            return ("quit", None)
        if cmd == "1":
            new = prompt("New download dir:")
            if new:
                p = Path(new).expanduser().resolve()
                try:
                    p.mkdir(parents=True, exist_ok=True)
                    app.state.set_setting("download_dir", str(p))
                except OSError as exc:
                    print(f"  could not create: {exc}")
                    prompt("(enter to continue)")
            return ("noop", None)
        if cmd == "2":
            new = prompt("Max results to display per page:")
            n = parse_int(new)
            if n and n > 0:
                app.state.set_setting("max_results", n)
            return ("noop", None)
        return ("noop", None)


class HistoryScreen(Screen):
    title = "history"

    def render(self, app):
        clear()
        header("Recent searches")
        history = app.state.history()
        print()
        if not history:
            print("  No search history yet.")
        else:
            for i, q in enumerate(history, 1):
                print(f"  {i:>3}) {truncate(q, 80)}")
        footer_hint("[#] repeat   [c] clear")
        cmd = prompt(">").lower()
        if cmd in ("b", "back"):
            return ("pop", None)
        if cmd in ("q", "quit"):
            return ("quit", None)
        if cmd == "c":
            app.state._data["history"] = []
            app.state.save()
            return ("noop", None)
        idx = parse_int(cmd)
        if idx is not None and 1 <= idx <= len(history):
            line = history[idx - 1]
            tokens = _shlex_split(line)
            recent_seconds = None
            terms: list[str] = []
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                if tok in ("-r", "--recent") and i + 1 < len(tokens):
                    try:
                        recent_seconds = search.parse_duration(tokens[i + 1])
                    except ValueError:
                        pass
                    i += 2
                    continue
                terms.append(tok)
                i += 1
            if not terms:
                return ("noop", None)
            query = search.build_query(terms)
            return ("push", SearchRunning(query=query, recent_seconds=recent_seconds))
        return ("noop", None)


# ── helpers ────────────────────────────────────────────────────────────

def _shlex_split(line: str) -> list[str]:
    import shlex
    try:
        return shlex.split(line)
    except ValueError:
        return line.split()


_DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]


def _coerce_magnet(text: str) -> str | None:
    """Accept a full magnet link OR a bare 40-char hex info hash. Return a
    valid magnet, or None if the input isn't recognisable."""
    import re
    text = text.strip()
    if text.startswith("magnet:"):
        return text
    # 40-char hex info hash
    if re.fullmatch(r"[a-fA-F0-9]{40}", text):
        trackers = "&".join(f"tr={t}" for t in _DEFAULT_TRACKERS)
        return f"magnet:?xt=urn:btih:{text.lower()}&{trackers}"
    return None


def _copy_to_clipboard(text: str) -> None:
    """Best-effort clipboard copy. Silent failure if no command works."""
    if not text:
        return
    candidates = []
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
    elif os.name == "nt":
        candidates = [["clip"]]
    else:
        candidates = [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    import subprocess
    for cmd in candidates:
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            p.communicate(input=text.encode("utf-8"), timeout=2)
            print("  (copied to clipboard)")
            time.sleep(0.4)
            return
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
    print("  (no clipboard tool found — printed link above)")


# ── app ────────────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self.state = State()
        self.engines = search.load_engines()
        self.downloader = DownloadManager(self.state)
        self.stack: list[Screen] = [MainMenu()]

    def run(self) -> None:
        try:
            while self.stack:
                screen = self.stack[-1]
                action, payload = screen.render(self)
                if action == "push" and payload is not None:
                    self.stack.append(payload)
                elif action == "pop":
                    if len(self.stack) > 1:
                        self.stack.pop()
                    else:
                        break
                elif action == "replace" and payload is not None:
                    self.stack[-1] = payload
                elif action == "quit":
                    break
                elif action == "noop":
                    continue
        finally:
            self.downloader.shutdown()
            self.state.save()
            print("\nbye.")


def main() -> int:
    app = App()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
