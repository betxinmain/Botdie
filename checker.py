import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Tuple, Dict
import requests

TIKTOK_ENDPOINT = "https://www.tiktok.com/@{}"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

def _headers():
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
    }

def _normalize(u: str) -> str:
    u = u.strip()
    if not u:
        return ""
    if u.startswith("http"):
        import re as _re
        m = _re.search(r"/@([A-Za-z0-9._]+)", u)
        return m.group(1) if m else u.split("/")[-1]
    if u.startswith("@"):
        return u[1:]
    return u

def check_one(username: str, timeout: float = 10.0, session: requests.Session = None) -> Tuple[str, str]:
    u = _normalize(username)
    if not u:
        return (username, "error")
    url = TIKTOK_ENDPOINT.format(u)
    s = session or requests.Session()
    try:
        r = s.get(url, headers=_headers(), timeout=timeout, allow_redirects=True)
        code = r.status_code
        if code == 200:
            txt = r.text.lower()
            if "couldn't find this account" in txt or "không tìm thấy tài khoản này" in txt:
                return (u, "banned")
            return (u, "live")
        elif code == 404:
            return (u, "banned")
        else:
            return (u, "error")
    except requests.RequestException:
        return (u, "error")

def check_usernames(usernames: Iterable[str], threads: int = 5, timeout: float = 10.0) -> Dict[str, List[str]]:
    usernames = [u for u in map(_normalize, usernames) if u]
    buckets = {"live": [], "banned": [], "error": []}
    if not usernames:
        return buckets
    threads = max(1, min(threads, 5))
    with requests.Session() as s, ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {ex.submit(check_one, u, timeout, s): u for u in usernames}
        for f in as_completed(futures):
            u, status = f.result()
            buckets.setdefault(status, []).append(u)
            time.sleep(random.uniform(0.05, 0.15))
    return buckets