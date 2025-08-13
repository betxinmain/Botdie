import os
import re
import time
import html
from typing import List, Tuple, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

LIVE = "live"
DIE = "die"
ERROR = "error"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.tiktok.com/",
}

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_DEFAULT_HEADERS.copy())
    s.timeout = 10
    return s

def _normalize(username: str) -> str:
    u = (username or "").strip()
    if u.startswith("@"):
        u = u[1:]
    # keep only allowed chars: letters, numbers, underscore, dot
    u = re.sub(r"[^A-Za-z0-9._]", "", u)
    return u

def _looks_like_live(html_text: str) -> bool:
    if not html_text:
        return False
    t = html_text
    # Common signals on live accounts
    if 'property="og:url"' in t and 'content="https://www.tiktok.com/@' in t:
        return True
    if '"followers"' in t or '"following"' in t or '"videoCount"' in t:
        return True
    # Some localized UI strings
    if "Followers" in t or "Following" in t or "likes" in t.lower():
        return True
    return False

def _looks_like_die(html_text: str) -> bool:
    if not html_text:
        return False
    t = html.unescape(html_text).lower()
    signals = [
        "couldn't find this account",
        "this account is unavailable",
        "account suspended",
        "account banned",
        "page not available",
        "page not found",
        "không thể tìm thấy tài khoản",  # vi
    ]
    return any(sig in t for sig in signals)

def check_username(username: str, session: requests.Session = None, timeout: int = 12) -> Tuple[str, str]:
    """
    Return (username, status) with status in {LIVE, DIE, ERROR}
    """
    u = _normalize(username)
    if not u:
        return (username, ERROR)
    session = session or _make_session()
    url = f"https://www.tiktok.com/@{u}"
    try:
        resp = session.get(url, allow_redirects=True, timeout=timeout)
        # Handle rate limiting / gateway issues first
        if resp.status_code in (429, 430, 502, 503, 504):
            return (u, ERROR)
        if resp.status_code == 404:
            return (u, DIE)
        txt = resp.text or ""
        if _looks_like_die(txt):
            return (u, DIE)
        if _looks_like_live(txt):
            return (u, LIVE)
        # Fallback: if redirected to search or error pages
        final_url = (resp.url or "").lower()
        if "/search/" in final_url or "404" in final_url:
            return (u, DIE)
        # Unknown layout -> treat as ERROR to be safe
        return (u, ERROR)
    except requests.RequestException:
        return (u, ERROR)

def check_many(usernames: List[str], threads: int = 5, delay_between: float = 0.0) -> Dict[str, List[str]]:
    """
    Check a list of usernames concurrently.
    Returns dict with keys LIVE/DIE/ERROR and list of usernames.
    """
    usernames = [u for u in map(_normalize, usernames) if u]
    out = {LIVE: [], DIE: [], ERROR: []}
    if not usernames:
        return out

    session = _make_session()

    with ThreadPoolExecutor(max_workers=max(1, min(threads, 16))) as ex:
        futures = {}
        for i, u in enumerate(usernames):
            if delay_between and i:
                time.sleep(delay_between)
            futures[ex.submit(check_username, u, session)] = u
        for fut in as_completed(futures):
            u, status = fut.result()
            out.get(status, out[ERROR]).append(u)

    return out

def write_results(results: Dict[str, List[str]], folder: str = "results") -> Dict[str, str]:
    os.makedirs(folder, exist_ok=True)
    paths = {}
    for key in (LIVE, DIE, ERROR):
        path = os.path.join(folder, f"{key}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(results.get(key, [])))
        paths[key] = path
    return paths
