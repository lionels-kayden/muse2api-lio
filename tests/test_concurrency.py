"""Kiểm thử bộ điều phối đồng thời đa tài khoản và khả năng cách ly."""
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Đảm bảo import muse2api trong thư mục hiện tại
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

import app
from config import Config
from store import Store


def test_concurrency_lock():
    lock = app.ConcurrencyLock(limit=2)
    assert not lock.locked()

    # Slot thứ nhất
    assert lock.acquire(timeout=0.1)
    assert lock.locked()

    # Slot thứ hai (đồng thời = 2)
    assert lock.acquire(timeout=0.1)
    assert lock.locked()

    # Slot thứ ba phải quá hạn chờ
    assert not lock.acquire(blocking=True, timeout=0.05)

    # Giải phóng một slot
    lock.release()
    assert lock.locked()

    # Giờ slot thứ ba đã lấy được
    assert lock.acquire(timeout=0.1)

    lock.release()
    lock.release()
    assert not lock.locked()
    print("PASS: test_concurrency_lock")


def test_account_pool_manager():
    dummy_accounts = [
        {"id": "acc-1", "label": "A1", "enabled": True, "cookies": {"k": "v1"}},
        {"id": "acc-2", "label": "A2", "enabled": True, "cookies": {"k": "v2"}},
    ]
    dummy_store = SimpleNamespace(
        accounts=dummy_accounts,
        get_account=lambda aid: next((a for a in dummy_accounts if a["id"] == aid), None),
        pick_account=lambda preferred_id=None, rotate=True, force_rotate=False, exclude_ids=None, record_use=True: next(
            (a for a in dummy_accounts if a["id"] not in (exclude_ids or set())), None
        )
    )

    pool = app.AccountPoolManager(dummy_store)

    # Luồng 1 chiếm acc-1
    a1 = pool.acquire(timeout=0.5)
    assert a1["id"] == "acc-1"
    assert "acc-1" in pool.busy_accounts

    # Luồng 2 chiếm acc-2 song song (tự né acc-1 đang bận)
    a2 = pool.acquire(timeout=0.5)
    assert a2["id"] == "acc-2"
    assert "acc-2" in pool.busy_accounts

    # Lúc này cả hai tài khoản đều bận, luồng 3 phải chờ và quá hạn
    t0 = time.monotonic()
    try:
        pool.acquire(timeout=0.1)
        raise AssertionError("Phải ném ra lỗi quá hạn chờ")
    except app.MuseGenerationError:
        pass
    assert time.monotonic() - t0 >= 0.08

    # Sau khi giải phóng acc-1, luồng 3 lấy được acc-1 ngay lập tức
    pool.release(a1["id"])
    assert "acc-1" not in pool.busy_accounts

    a3 = pool.acquire(timeout=0.5)
    assert a3["id"] == "acc-1"

    # Dọn dẹp giải phóng
    pool.release(a2["id"])
    pool.release(a3["id"])
    assert len(pool.busy_accounts) == 0
    print("PASS: test_account_pool_manager")


def test_parallel_generation_simulation():
    """Mô phỏng hai yêu cầu dùng hai tài khoản khác nhau sinh tạo song song."""
    dummy_accounts = [
        {"id": "acc-1", "label": "A1", "enabled": True, "cookies": {"c": "1"}},
        {"id": "acc-2", "label": "A2", "enabled": True, "cookies": {"c": "2"}},
    ]
    orig_accounts = list(app.store.accounts)
    running_threads = set()
    active_accounts = []
    concurrency_observed = False
    lock = threading.Lock()

    def fake_generate(cookies, prompt, expect, timeout, expires=None, account_id=None, on_progress=None, reference_image=None):
        nonlocal concurrency_observed
        t_name = threading.current_thread().name
        with lock:
            running_threads.add(t_name)
            active_accounts.append(account_id)
            if len(running_threads) >= 2:
                concurrency_observed = True
        time.sleep(0.15)
        with lock:
            running_threads.discard(t_name)
        return {"filename": f"{account_id}.png", "size": 1234, "kind": expect}

    try:
        with patch("store._write", lambda *a, **k: None), \
             patch.object(app.store, "mark", lambda *a, **k: None), \
             patch.object(app.engine, "start", return_value=None), \
             patch.object(app.engine, "generate", side_effect=fake_generate), \
             patch.object(app, "_renew_and_persist", return_value=None), \
             patch.object(app, "_sync_cookies", return_value=None):

            app.store.accounts = dummy_accounts
            results = []
            def run_task(p):
                res = app._run_generation(p, "image", timeout=5)
                results.append(res)

            t1 = threading.Thread(target=run_task, args=("task 1",), name="Worker-1")
            t2 = threading.Thread(target=run_task, args=("task 2",), name="Worker-2")

            t1.start()
            t2.start()
            t1.join()
            t2.join()

            assert len(results) == 2
            assert concurrency_observed, "Hai tác vụ phải chạy song song đồng thời trên hai luồng độc lập!"
            assert set(r[1] for r in results) == {"acc-1", "acc-2"}, "Hai tác vụ phải được điều phối trên hai tài khoản độc lập!"
            assert len(app.ACCOUNT_POOL.busy_accounts) == 0, "Sau khi tác vụ kết thúc, mọi tài khoản phải được hoàn trả triệt để!"
    finally:
        app.store.accounts = orig_accounts
    print("PASS: test_parallel_generation_simulation")


if __name__ == "__main__":
    test_concurrency_lock()
    test_account_pool_manager()
    test_parallel_generation_simulation()
    print("ALL CONCURRENCY TESTS PASSED!")
