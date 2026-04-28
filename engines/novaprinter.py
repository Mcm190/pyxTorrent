import threading

# Shared result store — qsearch.py reads from this after searches finish
results: list = []
_lock = threading.Lock()


def prettyPrinter(result: dict) -> None:
    """Called by each engine to emit a result. We capture instead of printing."""
    with _lock:
        results.append({
            "name": str(result.get("name", "")).strip(),
            "link": str(result.get("link", "")).strip(),
            "size": str(result.get("size", "-1")).strip(),
            "seeds": _to_int(result.get("seeds", -1)),
            "leech": _to_int(result.get("leech", -1)),
            "engine_url": str(result.get("engine_url", "")).strip(),
            "desc_link": str(result.get("desc_link", "")).strip(),
        })


def _to_int(value) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return -1
