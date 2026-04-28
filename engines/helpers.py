import datetime
import gzip
import html
import http.client
import io
import urllib.error
import urllib.request


def _browser_ua() -> str:
    base_date = datetime.date(2024, 4, 16)
    base_version = 125
    now = datetime.date.today()
    version = base_version + ((now - base_date).days // 30)
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{version}.0) Gecko/20100101 Firefox/{version}.0"


def retrieve_url(url: str) -> str:
    request = urllib.request.Request(url, None, {"User-Agent": _browser_ua()})
    try:
        response: http.client.HTTPResponse = urllib.request.urlopen(request, timeout=15)
    except (urllib.error.HTTPError, urllib.error.URLError, Exception):
        return ""

    data = response.read()

    if data[:2] == b"\x1f\x8b":
        with io.BytesIO(data) as stream, gzip.GzipFile(fileobj=stream) as gz:
            data = gz.read()

    charset = "utf-8"
    try:
        charset = response.getheader("Content-Type", "").split("charset=", 1)[1]
    except IndexError:
        pass

    text = data.decode(charset, "replace")
    text = text.replace("&quot;", '\\"')
    return html.unescape(text)


# Some engines do `import helpers` and call helpers.retrieve_url directly
htmlentitydecode = html.unescape


def download_file(url: str) -> str:
    """Download a .torrent file to a temp location and return the path."""
    import tempfile
    request = urllib.request.Request(url, None, {"User-Agent": _browser_ua()})
    try:
        response = urllib.request.urlopen(request, timeout=15)
        suffix = ".torrent"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(response.read())
            return tmp.name
    except Exception:
        return ""
