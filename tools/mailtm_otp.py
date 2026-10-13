#!/usr/bin/env python3
"""Mail.tm email tạm thời: xin địa chỉ + polling lấy mã xác minh.

Công dụng: khi đăng nhập muse.ai cần mã xác minh gửi qua email, chạy script này
           để lấy một địa chỉ email tạm thời, điền vào trang đăng nhập, script sẽ
           in ra mã xác minh nhận được.

Không phụ thuộc gì (thuần thư viện chuẩn). Cách dùng:
    python3 mailtm_otp.py                # xin địa chỉ mới và bắt đầu polling
    python3 mailtm_otp.py --addr a@b.c --pass xxx   # tái sử dụng địa chỉ có sẵn
    python3 mailtm_otp.py --timeout 600  # thời gian polling (giây), mặc định 900
"""
import argparse, json, random, re, string, sys, time, urllib.request, urllib.error

API = "https://api.mail.tm"


def req(path, method="GET", token=None, body=None):
    """Gửi một request, trả về JSON đã được phân tích."""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Accept", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise SystemExit(f"[!] {method} {path} -> HTTP {e.code}: {detail}")


def new_account():
    """Xin một địa chỉ mới, trả về (address, password, token)."""
    d = req("/domains")
    domains = d["hydra:member"] if isinstance(d, dict) else d  # bản mới trả về mảng, bản cũ bọc trong hydra
    domain = next(x["domain"] for x in domains if x.get("isActive", True))
    user = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    addr, pwd = f"{user}@{domain}", "".join(random.choices(string.ascii_letters + string.digits, k=16))
    req("/accounts", "POST", body={"address": addr, "password": pwd})
    token = req("/token", "POST", body={"address": addr, "password": pwd})["token"]
    return addr, pwd, token


def login(addr, pwd):
    return req("/token", "POST", body={"address": addr, "password": pwd})["token"]


CODE_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


def poll(token, timeout):
    """Polling hộp thư mới, hễ thấy mã xác minh là in ra. Trả về mã hoặc None."""
    seen, deadline = set(), time.time() + timeout
    print(f"[*] Bắt đầu polling hộp thư đến (tối đa {timeout}s, Ctrl-C để dừng)\n")
    while time.time() < deadline:
        try:
            r = req("/messages?page=1", token=token)
            msgs = r["hydra:member"] if isinstance(r, dict) else r
        except SystemExit:
            time.sleep(3); continue
        for m in msgs:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            full = req(f"/messages/{m['id']}", token=token)
            frm = (full.get("from") or {}).get("address", "?")
            subj = full.get("subject", "")
            body = full.get("text") or full.get("intro") or ""
            print(f"[+] Email mới  từ {frm}\n    Tiêu đề: {subj}")
            codes = CODE_RE.findall(f"{subj}\n{body}")
            print(f"    Nội dung: {body.strip()[:500]}")
            if codes:
                print(f"\n>>> Mã xác minh: {codes[0]}\n")
                return codes[0]
            print("    (Chưa nhận diện được mã xác minh, tiếp tục chờ…)\n")
        left = int(deadline - time.time())
        print(f"    Đang chờ… còn {left}s", end="\r", flush=True)
        time.sleep(3)
    print("\n[!] Hết thời gian, chưa nhận được mã xác minh.")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr"); ap.add_argument("--pass", dest="pwd")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    if a.addr and a.pwd:
        addr, pwd, token = a.addr, a.pwd, login(a.addr, a.pwd)
        print(f"[*] Tái sử dụng địa chỉ có sẵn: {addr}")
    else:
        addr, pwd, token = new_account()
        print(f"[*] Đã xin địa chỉ mới: {addr}\n    Mật khẩu: {pwd} (giữ lại nếu muốn tái sử dụng)")
    print(f"\n=== Điền địa chỉ dưới đây vào trang đăng nhập muse.ai ===\n    {addr}\n")
    code = poll(token, a.timeout)
    sys.exit(0 if code else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] Đã dừng.")
