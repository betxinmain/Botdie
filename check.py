import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import os
import requests
from colorama import Fore, init

init(autoreset=True)

TIKTOK_ENDPOINT = "https://www.tiktok.com/@{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    "Connection": "keep-alive",
}

_lock = Lock()

def ensure_outfiles():
    os.makedirs("results", exist_ok=True)
    for fn in ("live.txt", "banned.txt", "errors.txt"):
        open(os.path.join("results", fn), "a", encoding="utf-8").close()

def classify(username: str, status: int, text: str) -> str:
    # Heuristic: 200 + username seen in HTML/JSON => live
    # 404/451 => not found / unavailable => banned/die
    # 302/301 usually redirect to profile or login; treat as live only with username proof
    if status == 200:
        if f'"uniqueId":"{username}"' in text or f"/@{username}" in text:
            return "live"
        # Sometimes 200 for placeholder or privacy page -> treat as banned if username not present
        return "banned"
    if status in (404, 451):
        return "banned"
    if status == 429:
        return "error"  # rate limited; user can rerun
    # Other codes -> error
    return "error"

def check_one(username: str, session: requests.Session, timeout: float = 10.0) -> str:
    url = TIKTOK_ENDPOINT.format(username)
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        status = r.status_code
        text = r.text if isinstance(r.text, str) else ""
        result = classify(username, status, text)
    except requests.RequestException:
        result = "error"

    with _lock:
        out = "live.txt" if result == "live" else "banned.txt" if result == "banned" else "errors.txt"
        with open(os.path.join("results", out), "a", encoding="utf-8") as f:
            f.write(username + "\n")
        color = Fore.GREEN if result == "live" else Fore.RED if result == "banned" else Fore.YELLOW
        print(f"[{color}{result.upper()}{Fore.RESET}] {username}")
    return result

def main(wordlist: str, threads: int = 5, timeout: float = 10.0):
    with open(wordlist, "r", encoding="utf-8") as f:
        usernames = [x.strip().lstrip("@") for x in f if x.strip()]
    ensure_outfiles()
    with requests.Session() as s, ThreadPoolExecutor(max_workers=threads) as ex:
        futures = [ex.submit(check_one, u, s, timeout) for u in usernames]
        for _ in as_completed(futures):
            pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kiểm tra tài khoản TikTok sống/chết (proxyless)")
    parser.add_argument("wordlist", help="Đường dẫn file chứa danh sách username (mỗi dòng 1 username)")
    parser.add_argument("-t", "--threads", type=int, default=5, help="Số luồng song song (mặc định 5)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout mỗi request (giây)")
    args = parser.parse_args()
    # An toàn: giới hạn threads tối đa 5 để giảm risk bị 429
    threads = min(args.threads, 5)
    main(args.wordlist, threads=threads, timeout=args.timeout)
