import argparse
import itertools
import string

def generate(length: int, letters_only: bool = False):
    # username chỉ gồm chữ thường và số (TikTok cho phép chữ, số, _, . nhưng ta giữ đơn giản)
    alphabet = string.ascii_lowercase + ("" if letters_only else string.digits)
    for tup in itertools.product(alphabet, repeat=length):
        s = "".join(tup)
        if letters_only or any(ch.isdigit() for ch in s):
            yield s

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Sinh danh sách username để kiểm tra")
    p.add_argument("length", type=int, help="Độ dài username cần sinh")
    p.add_argument("-l", "--letters-only", action="store_true", help="Chỉ sinh chữ, không kèm số")
    p.add_argument("-o", "--out", default="usernames.txt", help="File xuất ra (mặc định usernames.txt)")
    a = p.parse_args()
    with open(a.out, "w", encoding="utf-8") as f:
        for name in generate(a.length, a.letters_only):
            f.write(name + "\n")
    print(f"✅ Đã ghi vào {a.out}")
