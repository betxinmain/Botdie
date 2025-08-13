            # -*- coding: utf-8 -*-
            import os
            import re
            import time
            import requests
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from threading import Lock

            TIKTOK_ENDPOINT = "https://www.tiktok.com/@{}"
            HEADERS = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
                "Connection": "keep-alive",
            }

            LIVE = "live"
            DIE = "die"
            ERROR = "error"

            _result_lock = Lock()

            def _normalize_username(u: str) -> str:
                u = u.strip().lstrip("@").strip()
                return re.sub(r"[^a-zA-Z0-9._]", "", u)

            def _parse_status(html: str) -> str:
                # Heuristics:
                # - "Couldn't find this account" → die
                # - 404 page indicators → die
                # - "This account is currently suspended" / "banned" / "blocked" → die
                # - Otherwise if we find og:url/profile URL blocks → live
                lower = html.lower()
                if any(key in lower for key in [
                    "couldn't find this account",
                    "account not found",
                    "page not available",
                    "this page isn't available",
                    "page unavailable",
                    "http 404",
                    "sign up for tiktok"  # some 404 templates
                ]):
                    return DIE
                if any(k in lower for k in ["suspend", "suspended", "ban", "banned", "block", "blocked"]):
                    # Treat clearly banned/suspended as die
                    return DIE
                # If OG tags suggest a valid profile
                if 'property="og:url"' in lower or 'og:title' in lower or 'tt-user-header' in lower:
                    return LIVE
                # Fallback: if we can find "Followers" label
                if "followers" in lower or "following" in lower or "likes" in lower:
                    return LIVE
                return ERROR

            def check_one(username: str, session: requests.Session, timeout: float = 10.0) -> str:
                u = _normalize_username(username)
                if not u:
                    return ERROR
                url = TIKTOK_ENDPOINT.format(u)
                try:
                    r = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
                    # If redirected to search or safety page, still try parse body
                    if r.status_code == 404:
                        return DIE
                    if r.status_code in (200, 301, 302):
                        return _parse_status(r.text)
                    if r.status_code == 429:
                        # Rate limited; backoff a bit then retry once
                        time.sleep(1.5)
                        r2 = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
                        if r2.status_code == 404:
                            return DIE
                        if r2.status_code in (200, 301, 302):
                            return _parse_status(r2.text)
                        return ERROR
                    # Other codes → error
                    return ERROR
                except requests.RequestException:
                    return ERROR

            def check_many(usernames, threads: int = 5, timeout: float = 10.0):
                usernames = [_normalize_username(u) for u in usernames if _normalize_username(u)]
                results = {LIVE: [], DIE: [], ERROR: []}
                if not usernames:
                    return results
                threads = max(1, min(5, int(threads or 1)))  # cap to 5
                with requests.Session() as s, ThreadPoolExecutor(max_workers=threads) as ex:
                    fut_map = {ex.submit(check_one, u, s, timeout): u for u in usernames}
                    for fut in as_completed(fut_map):
                        u = fut_map[fut]
                        try:
                            status = fut.result()
                        except Exception:
                            status = ERROR
                        results[status].append(u)
                return results
