import re, json, random, time
from typing import Iterable, Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
UA_POOL = [DESKTOP_UA, MOBILE_UA]

def _rand_id(n=32):
    import string, random
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(UA_POOL),
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    })
    s.cookies.set("tt_webid_v2", _rand_id(32), domain=".tiktok.com")
    return s

def _normalize(u: str) -> str:
    u = u.strip()
    if not u: return ""
    if u.startswith("http"):
        m = re.search(r"/@([A-Za-z0-9._]+)", u)
        return m.group(1) if m else u.split("/")[-1]
    if u.startswith("@"): return u[1:]
    u = re.sub(r"[^A-Za-z0-9._]", "", u)
    return u[:24]

def _parse_json_safe(txt: str):
    try: return json.loads(txt)
    except Exception: return None

def _extract_sigi_state(html: str):
    m = re.search(r'<script\s+id="SIGI_STATE"\s*type="application/json">(.+?)</script>', html, re.DOTALL)
    if not m: return None
    return _parse_json_safe(m.group(1))

def _classify_from_sigi(sigi: dict, username: str):
    try:
        users = sigi.get("UserModule", {}).get("users", {})
        if username in users and users[username].get("secUid"):
            return "live"
        if sigi.get("UserModule", {}).get("userNotFound"):
            return "banned"
    except Exception:
        pass
    return None

def _check_via_api(s, u, base, debug):
    url = f"{base}/api/user/detail/?uniqueId={u}"
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{base}/@{u}",
    }
    try:
        r = s.get(url, headers=h, timeout=10, allow_redirects=True)
        debug.append(("api", url, r.status_code, r.headers.get("content-type","")))
        if r.status_code == 200:
            data = _parse_json_safe(r.text)
            if data and isinstance(data, dict):
                ui = (data.get("userInfo") or {}).get("user")
                if ui and ui.get("uniqueId"):
                    return "live"
                sc = data.get("statusCode")
                if sc in (10000, 10221, 10222, 10223):
                    return "banned"
        elif r.status_code == 404:
            return "banned"
        elif r.status_code in (401,403,429):
            return None
    except requests.RequestException:
        return None
    return None

def _check_via_html(s, u, base, debug):
    url = f"{base}/@{u}"
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": base + "/",
    }
    try:
        r = s.get(url, headers=h, timeout=10, allow_redirects=True)
        debug.append(("html", url, r.status_code, len(r.text)))
        if r.status_code == 404:
            return "banned"
        if r.status_code == 200 and r.text:
            sigi = _extract_sigi_state(r.text)
            if sigi:
                cls = _classify_from_sigi(sigi, u)
                if cls: return cls
            low = r.text.lower()
            if "couldn't find this account" in low or "không tìm thấy tài khoản này" in low:
                return "banned"
            if f'@{u}'.lower() in low and '"og:type" content="profile"' in low:
                return "live"
        elif r.status_code in (401,403,429):
            return None
    except requests.RequestException:
        return None
    return None

def check_one(username: str, timeout: float = 10.0, session=None, debug=None):
    u = _normalize(username)
    dbg = [] if debug is None else debug
    if not u: return (username, "error")
    s = session or _session()

    for base in ("https://m.tiktok.com","https://www.tiktok.com"):
        cls = _check_via_api(s, u, base, dbg)
        if cls: return (u, cls)

    for base in ("https://m.tiktok.com","https://www.tiktok.com"):
        cls = _check_via_html(s, u, base, dbg)
        if cls: return (u, cls)

    share_url = f"https://m.tiktok.com/node/share/user/@{u}"
    try:
        r = s.get(share_url, headers={"User-Agent": random.choice(UA_POOL), "Accept":"application/json,*/*"}, timeout=10)
        dbg.append(("api", share_url, r.status_code, r.headers.get("content-type","")))
        if r.status_code == 200:
            data = _parse_json_safe(r.text)
            if data and data.get("userData", {}).get("user"):
                return (u, "live")
    except requests.RequestException:
        pass

    return (u, "error")

def check_usernames(usernames: Iterable[str], threads: int = 5, timeout: float = 10.0) -> Dict[str, List[str]]:
    usernames = [u for u in map(_normalize, usernames) if u]
    out = {"live": [], "banned": [], "error": []}
    if not usernames: return out
    threads = max(1, min(threads, 5))
    with _session() as s, ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(check_one, u, timeout, s, []): u for u in usernames}
        for f in as_completed(futs):
            u, status = f.result()
            out.setdefault(status, []).append(u)
            time.sleep(random.uniform(0.05, 0.15))
    return out

def debug_username(username: str):
    dbg = []
    with _session() as s:
        u, status = check_one(username, session=s, debug=dbg)
    return u, status, dbg
