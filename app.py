"""muse2api — reverse proxy hạn mức hội thoại/sinh ảnh/sinh video của tài khoản miễn phí Muse(muse.ai) trên web thành API.

Tương thích OpenAI:
  POST /v1/chat/completions          (hội thoại văn bản / mã, hỗ trợ stream, dùng được với Codex)
  POST /v1/images/generations
  POST /v1/videos  +  GET /v1/videos/{task_id}
  GET  /v1/models
  GET  /v1/media/{name}

Quản trị (trang quản lý kho tài khoản ở GET /):
  GET    /admin/status
  GET    /admin/accounts
  POST   /admin/accounts            (một bản ghi / nhập hàng loạt dạng văn bản / mảng)
  PATCH  /admin/accounts/{id}       (đổi nhãn, bật/tắt)
  DELETE /admin/accounts/{id}
  POST   /admin/accounts/{id}/test  (mở muse.ai thật để kiểm tra phiên còn hiệu lực không)
  POST   /admin/accounts/{id}/relogin
  GET    /admin/tasks               (bản ghi tác vụ)
  DELETE /admin/tasks/{id}  |  POST /admin/tasks/clear
  GET    /admin/media  |  POST /admin/media/delete  |  DELETE /admin/media/{name}
  GET    /admin/extension           (zip tiện ích trình duyệt, dùng để lấy cookie)
  GET    /admin/cookie-helper       (script lấy cookie dòng lệnh, nâng cao)
"""
from __future__ import annotations

from typing import Any

import asyncio
import base64
import io
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
import zipfile

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel, Field

from config import CFG
from engine import ESSENTIAL_COOKIES, MuseAuthError, MuseEngine, MuseGenerationError
from store import Store, account_expiry, min_expiry

import sys
log = logging.getLogger("muse2api")
log.setLevel(logging.INFO)
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(_h)

CFG.ensure_dirs()
app = FastAPI(title="muse2api", version="1.5.4")

# Script trợ giúp Cookie phát yêu cầu nhập từ trang muse.ai, cần mở nguồn đó;
# tiện ích trình duyệt phát từ chrome-extension://, cũng mở luôn.
#
# Ở đây mở thẳng mọi nguồn: dịch vụ này dùng Bearer Key để xác thực, không phụ thuộc Cookie,
# mở nguồn không mang lại rủi ro vượt quyền; ngược lại nếu khóa cứng nguồn thì các agent
# phía trình duyệt (Open WebUI / LobeChat / đủ loại client Web) sẽ bị CORS chặn không dùng được.
_origins = [o.strip() for o in (CFG.cors_origins or "").split(",") if o.strip()]
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(CORSMiddleware,
                   allow_origins=["*"],
                   allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["*"],
                   expose_headers=["*"],
                   max_age=600)

store = Store(CFG)
engine = MuseEngine(CFG)


class ConcurrencyLock:
    """Khóa điều phối đồng thời đa tài khoản.
    Do CFG.max_concurrency giới hạn số slot đồng thời tối đa;
    đồng thời cung cấp giao diện acquire / release / locked tương thích với Lock chuẩn,
    đảm bảo tương thích ngược với các assertion khóa đơn trong lịch sử và thao tác phía quản trị."""
    def __init__(self, limit: int = 2):
        self.limit = max(1, limit)
        self._sem = threading.Semaphore(self.limit)
        self._count_lock = threading.Lock()
        self._active_count = 0

    def acquire(self, blocking: bool = True, timeout: float | None = -1) -> bool:
        if timeout is None:
            timeout = -1
        if timeout < 0:
            ok = self._sem.acquire(blocking=blocking)
        else:
            ok = self._sem.acquire(blocking=blocking, timeout=timeout)
        if ok:
            with self._count_lock:
                self._active_count += 1
        return ok

    def release(self):
        with self._count_lock:
            if self._active_count > 0:
                self._active_count -= 1
        self._sem.release()

    def locked(self) -> bool:
        with self._count_lock:
            return self._active_count > 0

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class AccountPoolManager:
    """Bộ quản lý xung đột đồng thời tài khoản: đảm bảo cùng một tài khoản không bị hai tác vụ đồng thời thao tác, nhiều tài khoản thì chạy song song."""
    def __init__(self, store_instance):
        self.store = store_instance
        self.busy_accounts: set[str] = set()
        self.cond = threading.Condition()

    def acquire(self, account_id: str | None = None, preferred_id: str | None = None,
                rotate: bool = True, force_rotate: bool = False,
                timeout: float = 60.0, record_use: bool = True) -> dict:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MuseGenerationError("Chờ tài khoản rảnh quá hạn, hiện mọi tài khoản đều đang bận")

                if account_id:
                    acc = self.store.get_account(account_id)
                    if not acc or not acc.get("enabled", True):
                        raise MuseAuthError(f"Tài khoản chỉ định {account_id} không tồn tại hoặc đã bị vô hiệu")
                    if account_id not in self.busy_accounts:
                        self.busy_accounts.add(account_id)
                        return acc
                else:
                    pref = preferred_id if preferred_id not in self.busy_accounts else None
                    acc = self.store.pick_account(
                        preferred_id=pref,
                        rotate=rotate,
                        force_rotate=force_rotate,
                        exclude_ids=self.busy_accounts,
                        record_use=record_use,
                    )
                    if acc:
                        self.busy_accounts.add(acc["id"])
                        return acc

                live = [a for a in self.store.accounts if a.get("enabled", True) and a.get("cookies")]
                if not live:
                    raise MuseAuthError("Không có tài khoản khả dụng, hãy nhập cookie trong trang quản trị trước")

                self.cond.wait(timeout=min(remaining, 0.5))

    def release(self, account_id: str | None):
        if not account_id:
            return
        with self.cond:
            self.busy_accounts.discard(account_id)
            self.cond.notify_all()


GEN_LOCK = ConcurrencyLock(limit=CFG.max_concurrency)
ACCOUNT_POOL = AccountPoolManager(store)
IMAGE_TASK_LOCK = threading.Lock()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ------------------------- Phản hồi lỗi kiểu OpenAI -------------------------
# Hầu hết các agent đều lấy thông tin lỗi theo dạng {"error": {"message": ...}} của OpenAI;
# FastAPI mặc định trả về {"detail": ...}, nên client sẽ không đọc được nguyên nhân và chỉ hiện "lỗi không xác định".
# Vì vậy /v1/* được chuyển đồng loạt sang định dạng OpenAI, còn endpoint quản trị giữ nguyên (giao diện phụ thuộc detail).
def _err_type(status: int) -> str:
    if status == 404:
        return "not_found_error"
    if status == 429:
        return "rate_limit_error"
    if status >= 500:
        return "server_error"
    return "invalid_request_error"


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    if request.url.path.startswith("/v1/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": str(exc.detail),
                               "type": _err_type(exc.status_code),
                               "param": None, "code": exc.status_code}},
            headers=getattr(exc, "headers", None))
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _validation_exc(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/v1/"):
        return JSONResponse(status_code=422, content={"error": {
            "message": "Xác thực tham số yêu cầu thất bại: " + str(exc.errors())[:400],
            "type": "invalid_request_error", "param": None, "code": 422}})
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

MODELS = [
    {"id": "muse-spark", "object": "model", "owned_by": "muse",
     "description": "Muse Spark —— hội thoại văn bản / mã (hạn mức miễn phí trên web, hỗ trợ streaming)"},
    {"id": "muse-image", "object": "model", "owned_by": "muse",
     "description": "Muse Image —— sinh ảnh từ văn bản / chỉnh sửa ảnh (hạn mức miễn phí trên web)"},
    {"id": "muse-video", "object": "model", "owned_by": "muse",
     "description": "Muse Video —— sinh video từ văn bản / từ ảnh (hạn mức miễn phí trên web)"},
]

# Hạ nguồn (Codex / Cline / đủ loại client) thường truyền tên model theo OpenAI, Anthropic,
# ở đây ánh xạ thống nhất về năng lực thật của muse.
#
# Lưu ý: muse.ai trên web là agent tự định tuyến, đối ngoại **không có danh sách model liệt kê được**,
# gọi ổn định được chỉ có ba năng lực thật —— Muse Spark (ngôn ngữ/mã),
# Muse Image (sinh ảnh), Muse Video (sinh video). Alias chỉ để hạ nguồn khỏi phải sửa cấu hình.
MODEL_ALIASES = {
    # ---- Văn bản / mã → muse-spark ----
    "muse-text": "muse-spark", "muse-chat": "muse-spark", "muse-llm": "muse-spark",
    "koda": "muse-spark",
    "gpt-3.5-turbo": "muse-spark", "gpt-4": "muse-spark", "gpt-4-turbo": "muse-spark",
    "gpt-4o": "muse-spark", "gpt-4o-mini": "muse-spark", "gpt-4.1": "muse-spark",
    "gpt-4.1-mini": "muse-spark", "gpt-5": "muse-spark", "gpt-5-codex": "muse-spark",
    "o1": "muse-spark", "o1-mini": "muse-spark", "o3": "muse-spark",
    "o3-mini": "muse-spark", "o4-mini": "muse-spark",
    "codex": "muse-spark", "codex-mini-latest": "muse-spark",
    "claude-3-5-sonnet": "muse-spark", "claude-3-5-sonnet-latest": "muse-spark",
    "claude-3-7-sonnet": "muse-spark", "claude-sonnet-4": "muse-spark",
    "claude-opus-4": "muse-spark", "claude-3-opus": "muse-spark",
    "claude-3-haiku": "muse-spark",
    "deepseek-chat": "muse-spark", "deepseek-coder": "muse-spark",
    "deepseek-reasoner": "muse-spark", "qwen-coder": "muse-spark",
    "gemini-2.5-pro": "muse-spark", "gemini-2.5-flash": "muse-spark",
    # ---- Sinh ảnh → muse-image ----
    "muse-img": "muse-image", "dall-e": "muse-image", "dall-e-3": "muse-image",
    "gpt-image-1": "muse-image", "flux": "muse-image", "midjourney": "muse-image",
    # ---- Sinh video → muse-video ----
    "muse-vid": "muse-video", "muse-videos": "muse-video",
    "sora": "muse-video", "sora-2": "muse-video", "veo": "muse-video",
    "veo-3": "muse-video", "kling": "muse-video", "runway": "muse-video",
}


def resolve_model(name: str | None, default: str = "muse-image") -> str:
    n = (name or "").strip().lower()
    return MODEL_ALIASES.get(n, n or default)


# ------------------------- Xác thực -------------------------
def auth(authorization: str | None = Header(default=None)):
    if not CFG.api_key:
        return True
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Thiếu Authorization: Bearer <key>")
    parts = authorization.split(None, 1)
    token = parts[1].strip() if len(parts) > 1 else ""
    if not token or token != CFG.api_key:
        raise HTTPException(401, "API key không hợp lệ")
    return True


# ------------------------- Model yêu cầu -------------------------

def _renew_and_persist(acc_id: str, wake_vm: bool = True, force: bool = False) -> dict | None:
    """Gọi /api/session để gia hạn cookie tài khoản và ghi lại vào store, trả về dict tài khoản mới nhất.
    Tài khoản vừa gia hạn gần đây (trong 10 phút) và trạng thái bình thường thì tái dùng thẳng, tránh mỗi yêu cầu chặn 2~3 giây cho vòng HTTP."""
    acc = store.get_account(acc_id)
    if not acc or not acc.get("cookies"):
        return acc
    now = time.time()
    last_sync = max(
        int(acc.get("synced_at") or 0),
        int(acc.get("last_keepalive") or 0),
        int(engine._last_http_renew.get(acc_id, 0)),
    )
    if not force and acc.get("ok") is True and (now - last_sync) < 600:
        engine._last_http_renew[acc_id] = last_sync
        return acc
    try:
        res = engine.renew_session_http(acc["cookies"], acc.get("cookies_exp"), wake_vm=wake_vm)
        engine._last_http_renew[acc_id] = now
        if res.get("cookies"):
            store.update_account(acc_id, cookies=res["cookies"],
                                 cookies_exp=res.get("cookies_exp"),
                                 ok=True if res.get("ok") else acc.get("ok"),
                                 synced_at=int(now))
            store.touch_keepalive(acc_id, True, f"Phiên bình thường (VM: {res.get('vm_state') or 'RUNNING'})")
    except MuseAuthError as exc:
        store.mark(acc_id, False, str(exc))
        raise
    except Exception as exc:
        log.warning("Gia hạn trước qua HTTP cho tài khoản %s gặp lỗi: %s", acc_id, exc)
    return store.get_account(acc_id)


def safe_chat_stream(cookies: dict, prompt: str, expires: dict | None,
                     timeout: int, account_id: str | None):
    """Chạy chat_stream trong luồng riêng và giữ GEN_LOCK, đẩy ra ngoài qua Queue.
    Dù client hạ nguồn ngắt kết nối, gặp lỗi hay quá hạn lúc nào, stop_event + khối finally đảm bảo giải phóng GEN_LOCK 100% tức thì, tuyệt đối không deadlock.
    Nếu trước chữ đầu tiên gặp VM một tài khoản bị treo hoặc phiên bất thường, tự chuyển sang tài khoản khỏe tiếp theo và thử lại một lần."""
    import queue
    q = queue.Queue(maxsize=100)
    stop_event = threading.Event()

    def worker():
        cur_id = account_id
        cur_cookies = cookies
        cur_exp = expires
        last_exc = None
        leased_id = None
        try:
            for attempt in range(2):
                if stop_event.is_set():
                    return
                if attempt > 0:
                    if leased_id:
                        ACCOUNT_POOL.release(leased_id)
                        leased_id = None
                    try:
                        alt = ACCOUNT_POOL.acquire(rotate=True, force_rotate=True, timeout=timeout)
                    except Exception:
                        break
                    if not alt or alt["id"] == cur_id:
                        if alt:
                            leased_id = alt["id"]
                        break
                    cur_id = alt["id"]
                    leased_id = cur_id
                    cur_cookies = alt["cookies"]
                    cur_exp = alt.get("cookies_exp")
                    log.info("【Tự động đổi tài khoản hội thoại】Chuyển sang tài khoản dự phòng %s (%s) để thử lại...", alt.get("label"), cur_id)
                elif cur_id:
                    ACCOUNT_POOL.acquire(account_id=cur_id, timeout=timeout)
                    leased_id = cur_id

                yielded = False
                try:
                    if cur_id:
                        refreshed = _renew_and_persist(cur_id, wake_vm=True, force=(attempt > 0))
                        if refreshed:
                            cur_cookies = refreshed["cookies"]
                            cur_exp = refreshed.get("cookies_exp")
                    with GEN_LOCK:
                        if stop_event.is_set():
                            return
                        engine.start()
                        for chunk in engine.chat_stream(
                            cur_cookies, prompt, cur_exp, timeout,
                            account_id=cur_id, stop_event=stop_event
                        ):
                            yielded = True
                            q.put(("data", chunk))
                            if stop_event.is_set():
                                return
                    if cur_id:
                        store.mark(cur_id, True, "")
                        _sync_cookies(cur_id)
                    return
                except MuseAuthError as exc:
                    last_exc = exc
                    if cur_id:
                        store.mark(cur_id, False, str(exc))
                    engine.close_current_page()
                    if yielded:
                        break
                except Exception as exc:
                    last_exc = exc
                    try:
                        engine.reset_thread()
                    except Exception:
                        pass
                    if yielded:
                        break
            if last_exc is not None:
                q.put(("error", last_exc))
        finally:
            if leased_id:
                ACCOUNT_POOL.release(leased_id)
            engine.close_current_page()
            q.put(("done", None))

    threading.Thread(target=worker, daemon=True).start()

    try:
        while True:
            kind, val = q.get()
            if kind == "data":
                yield val
            elif kind == "error":
                raise val
            else:
                break
    finally:
        stop_event.set()


class ImageRequest(BaseModel):
    prompt: str
    model: str = "muse-image"
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None
    response_format: str = "url"      # url | b64_json
    timeout: int | None = Field(default=None, ge=1, le=600)
    extra: str | None = None
    image: Any = None
    images: list | None = None
    reference_image: str | None = None
    async_: bool = Field(False, alias="async")


class VideoRequest(BaseModel):
    prompt: str
    model: str = "muse-video"
    duration: int | None = None
    size: str | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    timeout: int | None = None
    extra: str | None = None
    image: Any = None
    image_url: Any = None
    reference_image: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list | None = None


class ChatRequest(BaseModel):
    """Yêu cầu OpenAI Chat Completions.

    Cần rộng rãi tối đa với client bên thứ ba: trường không biết thì bỏ qua hết (hành vi mặc định của Pydantic),
    chỉ đọc những gì thật sự dùng được —— nếu không, mỗi agent truyền tham số một kiểu là 422 ngay.
    """
    model: str = "muse-spark"
    messages: list[ChatMessage] = []
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    prompt: str | None = None       # tương thích client đặt thẳng prompt ở tầng trên cùng
    # Mấy trường dưới đây khai báo chỉ để "đọc được", phía muse.ai không xử lý tương ứng
    tools: list | None = None
    tool_choice: object | None = None
    response_format: object | None = None
    stream_options: object | None = None


class ResponsesRequest(BaseModel):
    """OpenAI Responses API (Codex bản mới mặc định đi qua endpoint này)."""
    model: str = "muse-spark"
    input: str | list | None = None
    instructions: str | None = None
    stream: bool = False
    max_output_tokens: int | None = None
    timeout: int | None = None
    tools: list | None = None
    store: bool | None = None


class AccountRequest(BaseModel):
    label: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    cookie_header: str | None = None
    batch: str | None = None          # văn bản nhiều dòng, mỗi dòng một tài khoản
    expires: dict[str, int] = Field(default_factory=dict)   # tên cookie -> mốc thời gian hết hạn


class AccountPatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None


# ------------------------- Dựng prompt -------------------------
def build_image_prompt(r: ImageRequest) -> str:
    has_ref = bool(r.reference_image or r.image or r.images)
    if has_ref:
        parts = [f"基于我本次上传附带的参考图片进行生图/编辑：{r.prompt.strip()}"]
    else:
        parts = [f"全新文生图创作（当前未提供任何参考图，请勿查找历史相册或向用户索要原图，直接根据文字描述从零绘制生成一张全新图片）：{r.prompt.strip()}"]
    ar = (r.aspect_ratio or "").strip().lower()
    sz = (r.size or "").strip().lower()

    if any(k in ar or k in sz for k in ("9:16", "9/16", "portrait", "竖屏", "720x1280", "1080x1920")):
        parts.append("【画面构图与比例要求】：严格 9:16 竖屏满屏画幅（9:16 vertical portrait aspect ratio，高大于宽的手机全屏竖版画面），绝对不要生成横屏，保持垂直构图")
    elif any(k in ar or k in sz for k in ("16:9", "16/9", "landscape", "横屏", "1280x720", "1920x1080")):
        parts.append("【画面构图与比例要求】：16:9 宽屏横屏画幅（16:9 widescreen landscape aspect ratio）")
    elif any(k in ar or k in sz for k in ("1:1", "square", "正方形", "1024x1024")):
        parts.append("【画面构图与比例要求】：1:1 正方形画幅（1:1 square aspect ratio）")
    elif any(k in ar or k in sz for k in ("4:3", "4/3")):
        parts.append("【画面构图与比例要求】：4:3 比例画幅")
    elif any(k in ar or k in sz for k in ("3:4", "3/4")):
        parts.append("【画面构图与比例要求】：3:4 竖向画幅")
    elif r.aspect_ratio:
        parts.append(f"【画面构图与比例要求】：{r.aspect_ratio} 画面比例")
    elif r.size:
        parts.append(f"尺寸/比例：{r.size}")

    if has_ref:
        parts.append("【纯净画面要求】：彻底清除并去除参考图中的所有文字、水印、签名、角标及Logo标记（clean image without any watermark, text, or logo），输出绝对纯净无字画面")

    if r.extra:
        parts.append(r.extra)
    return "，".join(parts)


def _content_text(content) -> str:
    """Chuẩn hóa content của OpenAI thành văn bản thuần (tương thích dạng list đa phương thức)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type")
                if t in (None, "text", "input_text", "output_text"):
                    parts.append(str(item.get("text") or ""))
                elif t == "image_url":
                    parts.append("[图片]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return str(content)


def build_chat_prompt(messages: list[ChatMessage]) -> str:
    """Ghép messages thành một prompt gửi tới muse.ai.

    Bản thân trang muse.ai là một phiên có ngữ cảnh, nhưng API này là stateless (mỗi lần có thể rơi vào
    tài khoản/trang khác nhau), nên ghép lịch sử vào prompt là kiểm soát được nhất —— đúng khớp với kiểu
    client "mỗi lượt đều mang toàn bộ lịch sử" như Codex.
    """
    system, turns = [], []
    for m in messages:
        role = (m.role or "").strip().lower()
        text = _content_text(m.content).strip()
        if role == "tool":
            # Kết quả thực thi công cụ → gửi đi như "thông tin do người dùng cung cấp"
            if text:
                turns.append(("user", "【工具执行结果】\n" + text))
            continue
        if not text:
            # Một số client có message assistant chỉ mang tool_calls, không có nội dung chính
            if role == "assistant" and m.tool_calls:
                turns.append(("assistant", "【请求调用工具】" + json.dumps(
                    m.tool_calls, ensure_ascii=False)[:600]))
            continue
        if role in ("system", "developer"):
            system.append(text)
        else:
            turns.append((role, text))

    # Một lượt và không có chỉ thị hệ thống → gửi thẳng nguyên văn, sát hội thoại tự nhiên nhất
    if len(turns) == 1 and not system and turns[0][0] == "user":
        return turns[0][1]

    parts = []
    if system:
        sys_text = "\n\n".join(system)
        sys_text = sys_text.replace("danger-full-access", "standard-workspace-access")
        parts.append(f"背景与任务设定：\n{sys_text}")
    for role, text in turns:
        label = "助手" if role == "assistant" else "用户"
        parts.append(f"{label}：\n{text}")
    return "\n\n".join(parts)


# ------------------------- Thích ứng gọi công cụ (function calling) -------------------------
# Model trên web của muse.ai **không** trả về tool_calls có cấu trúc, nên ở đây làm một lớp thích ứng giao thức:
#   1) Khi request mang tools, dịch định nghĩa công cụ thành【giao thức gọi công cụ】trong prompt;
#   2) Model xuất ```json {"tool": "...", "arguments": {...}} ``` theo giao thức;
#   3) Ta phân tích đoạn này trở lại thành tool_calls của OpenAI để giao cho agent hạ nguồn.
#
# Lưu ý: đây là "thích ứng cố hết sức" chứ không đảm bảo —— model mục tiêu là model hội thoại tổng quát, không được
# tinh chỉnh riêng cho function calling, mức độ tuân thủ giao thức cần quan sát thực đo.
_TOOL_PROTOCOL_HEAD = """你可以根据需要调用以下工具来协助用户完成任务。
若需调用工具，请直接输出如下格式的 JSON 代码块（不要包含其他多余解释）：
```json
{"name": "<工具名>", "arguments": {<参数>}}
```
如果需要调用多个工具，请输出包含多个对象的 JSON 数组。
如果无需调用工具，请直接用自然语言回答。

可用工具列表：
"""


def _describe_params(params) -> str:
    """Mô tả tham số JSON Schema thành văn bản nhiều dòng dễ đọc."""
    if not isinstance(params, dict):
        return "      (không có tham số)"
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    if not props:
        return "      (không có tham số)"
    lines = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        lines.append("      - %s (%s, %s)%s" % (
            name, spec.get("type") or "any",
            "bắt buộc" if name in required else "tùy chọn",
            (" " + spec["description"]) if spec.get("description") else ""))
    return "\n".join(lines)


def build_tools_prompt(tools: list | None) -> str:
    """Dịch định nghĩa tools thành đoạn prompt (không có công cụ thì trả về chuỗi rỗng).

    Đồng thời tương thích `{"type":"function","function":{...}}` của Chat Completions
    và `{"type":"function","name":...,"parameters":...}` của Responses API.
    """
    if not tools:
        return ""
    items = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        if not fn.get("name"):
            continue
        desc = fn.get("description") or ""
        items.append("%d. %s%s\n   Tham số:\n%s" % (
            len(items) + 1, fn["name"], (" — " + desc) if desc else "",
            _describe_params(fn.get("parameters"))))
    if not items:
        return ""
    return _TOOL_PROTOCOL_HEAD + "\n".join(items) + "\n"


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")


def _as_tool_calls(obj) -> list[dict] | None:
    """Chuyển JSON đã phân tích thành tool_calls của OpenAI; không giống gọi công cụ thì trả về None."""
    raw = obj if isinstance(obj, list) else [obj]
    if not raw:
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        name = item.get("tool") or item.get("name") or item.get("function")
        if not isinstance(name, str) or not name:
            return None
        args = item.get("arguments")
        if args is None:
            args = item.get("parameters") or item.get("args") or {}
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        out.append({"id": "call_" + uuid.uuid4().hex[:20], "type": "function",
                    "function": {"name": name, "arguments": args}})
    return out or None


def parse_tool_calls(text: str) -> tuple[list[dict] | None, str]:
    """Trích gọi công cụ từ đầu ra của model.

    Trả về (tool_calls, văn bản còn lại); không trích được thì trả về (None, nguyên văn).
    Hỗ trợ nhiều dạng:
    1) Khối code ```json ... ```;
    2) JSON trần (hoặc có tiền tố từ khóa json/JSON);
    3) Đối tượng hoặc mảng JSON hoàn chỉnh nằm lẫn trong văn bản.
    """
    if not text:
        return None, text
    for m in reversed(list(_FENCE_RE.finditer(text))):
        try:
            calls = _as_tool_calls(json.loads(m.group(1).strip()))
        except Exception:
            continue
        if calls:
            return calls, (text[:m.start()] + text[m.end():]).strip()

    stripped = text.strip()
    clean_stripped = re.sub(r"^(?:```)?(?:json|JSON)?\s*", "", stripped).rstrip("`").strip()
    if clean_stripped.startswith(("{", "[")):
        try:
            calls = _as_tool_calls(json.loads(clean_stripped))
            if calls:
                return calls, ""
        except Exception:
            pass

    m_json = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if m_json:
        try:
            calls = _as_tool_calls(json.loads(m_json.group(1).strip()))
            if calls:
                rest = (text[:m_json.start()] + text[m_json.end():]).strip()
                if re.sub(r"^(?:```)?(?:json|JSON)?\s*", "", rest).strip("`").strip() == "":
                    rest = ""
                return calls, rest
        except Exception:
            pass

    return None, text


def build_video_prompt(r: VideoRequest) -> str:
    user_prompt = r.prompt.strip()
    ar = (r.aspect_ratio or "").strip().lower()
    sz = (r.size or "").strip().lower()
    dur = r.duration or 6
    has_ref = bool(r.reference_image or r.image or r.image_url)

    is_vertical = any(k in ar or k in sz for k in ("9:16", "9/16", "portrait", "竖屏", "720x1280", "1080x1920"))

    parts = []
    if is_vertical:
        if has_ref:
            parts.append(f"基于我本次上传的参考图片附件作为第一帧参考图（严禁使用历史图片或任何其他图像，必须严格以我当前刚刚上传并附带在此处的这张图片为起始帧）：生成一个严格9:16竖屏手机满屏的动态图生视频（9:16 vertical portrait video，720x1280，高大于宽的手机全屏竖版画面，严格以附带的参考图为起始第一帧延续动作，严禁生成横屏或黑边，时长严格为 {dur} 秒）：{user_prompt}")
        else:
            parts.append(f"全新文生视频创作（纯文本全新生成，严禁参考任何历史图片或上下文）：生成一个严格9:16竖屏手机满屏视频（9:16 vertical portrait video，720x1280，高大于宽的手机全屏竖版画面，严禁生成横屏或带有左右黑边，保持垂直构图，时长严格为 {dur} 秒）：{user_prompt}")
    elif any(k in ar or k in sz for k in ("16:9", "16/9", "landscape", "横屏", "1280x720", "1920x1080")):
        if has_ref:
            parts.append(f"基于我本次上传的参考图片附件作为第一帧参考图（严禁使用历史图片或任何其他图像，必须严格以我当前刚刚上传并附带在此处的这张图片为起始帧）：生成一个16:9宽屏横屏图生视频（16:9 widescreen landscape video，严格以附带的参考图为起始第一帧延续动作，时长严格为 {dur} 秒）：{user_prompt}")
        else:
            parts.append(f"全新文生视频创作（纯文本全新生成，严禁参考任何历史图片或上下文）：生成一个16:9横屏宽屏视频（16:9 widescreen landscape video，时长严格为 {dur} 秒）：{user_prompt}")
    else:
        if has_ref:
            parts.append(f"基于我本次上传的参考图片附件作为第一帧参考图（严禁使用历史图片或任何其他图像，必须严格以我当前刚刚上传并附带在此处的这张图片为起始帧）：生成动态图生视频（严格以附带的参考图为起始第一帧延续动作，时长严格为 {dur} 秒）：{user_prompt}")
        else:
            parts.append(f"全新文生视频创作（纯文本全新生成，严禁参考任何历史图片或上下文）：生成一个视频（时长严格为 {dur} 秒）：{user_prompt}")

    if r.resolution:
        parts.append(f"画质规格：{r.resolution}")
    if r.extra:
        parts.append(r.extra)
    return "，".join(parts)


def media_url(name: str) -> str:
    """Địa chỉ media.

    Cấu hình public_base thì trả về **URL tuyệt đối** —— client tương thích OpenAI (và các nền tảng agent)
    nhận data[].url xong thường render hoặc tải trực tiếp, đường dẫn tương đối sẽ bị phân giải về tên miền
    của chính client, gây 404. public_base rỗng thì lùi về đường dẫn tương đối.
    """
    base = _public_base()
    return f"{base}/v1/media/{name}" if base else f"/v1/media/{name}"


# ------------------------- Phân tích cookie -------------------------
def parse_cookie_text(text: str) -> dict[str, str]:
    """Phân tích `a=1; b=2` / JSON / dòng Set-Cookie thành dict."""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        import json
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return {str(k): str(v) for k, v in obj.items()}
        except Exception:  # noqa: BLE001
            pass
    out: dict[str, str] = {}
    for part in re.split(r"[;\n]+", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def parse_batch(text: str) -> list[tuple[str, dict]]:
    """Nhập hàng loạt: mỗi dòng `nhãn | chuỗi cookie`, nhãn có thể bỏ qua."""
    out: list[tuple[str, dict]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label = ""
        body = line
        if "|" in line:
            head, tail = line.split("|", 1)
            if "=" not in head:          # `|` xuất hiện trong cookie thì không tính là dấu phân cách
                label, body = head.strip(), tail.strip()
        cookies = parse_cookie_text(body)
        if cookies:
            out.append((label, cookies))
    return out


# ------------------------- Sinh tạo lõi -------------------------
def _sync_cookies(acc_id: str) -> dict:
    """Đọc lại cookie từ trình duyệt sau khi sinh tạo rồi ghi vào kho tài khoản.

    Kết luận thực đo: **expires của cookie cốt lõi không được gia hạn nhờ hành vi sử dụng** (hatch_vml cố định
    hết hạn sau khoảng 2 ngày), ở đây đồng bộ về chủ yếu là các cookie phi cốt lõi được cấp lại mỗi lần truy cập
    (_fbp / wd / dpr...) cùng các mục có thể mới phát sinh, tránh thông tin tài khoản cũ hơn thực tế.

    Lưu ý: hàm này **tuyệt đối không được làm hỏng kết quả sinh tạo** —— sinh tạo đã thành công rồi,
    đồng bộ chỉ là điểm thêm, có vấn đề gì cũng chỉ ghi một dòng log.
    """
    try:
        live = engine.read_cookies()
        if not live:
            return {"synced": 0}
        acc = store.get_account(acc_id)
        if not acc:
            return {"synced": 0}
        cur = dict(acc.get("cookies") or {})
        new_vals = {k: v["value"] for k, v in live.items() if v.get("value")}
        # Thời hạn phải "hợp nhất" chứ không "ghi đè": expires đọc về của cookie phiên là -1,
        # nếu ghi đè toàn bộ sẽ xóa sạch bản ghi thời hạn sẵn có (thực đo đã vấp lỗi này).
        exps = dict(acc.get("cookies_exp") or {})
        exps.update({k: v["expires"] for k, v in live.items()
                     if _pos(v.get("expires"))})
        changed = sum(1 for k, v in new_vals.items() if cur.get(k) != v)
        store.update_account(acc_id, cookies={**cur, **new_vals},
                             cookies_exp=exps, synced_at=int(time.time()))
        updated = store.get_account(acc_id) or {}
        return {"synced": len(new_vals), "changed": changed,
                "expires_at": updated.get("expires_at")}
    except Exception as exc:  # noqa: BLE001
        log.warning("Đồng bộ cookie thất bại (không ảnh hưởng lần sinh tạo này): %s", exc)
        return {"synced": 0, "error": str(exc)[:200]}


def _pos(v) -> bool:
    try:
        return int(v) > 0
    except (TypeError, ValueError):
        return False


def _run_generation(prompt: str, kind: str, timeout: int,
                    account_id: str | None = None, on_progress=None,
                    reference_image: str | None = None,
                    on_acquired=None) -> tuple[dict, str | None]:
    # Browser ownership covers account selection, retry and cleanup, not just generate().
    deadline = time.monotonic() + max(1, timeout)
    if not GEN_LOCK.acquire(timeout=max(1, timeout)):
        raise MuseGenerationError("Chờ hàng đợi trình duyệt quá hạn, hãy thử lại sau")
    try:
        if on_acquired:
            try:
                on_acquired()
            except Exception:
                pass
        return _run_generation_locked(prompt, kind, timeout, account_id,
                                      on_progress, reference_image, deadline=deadline)
    finally:
        GEN_LOCK.release()


def _run_generation_locked(prompt: str, kind: str, timeout: int,
                    account_id: str | None = None, on_progress=None,
                    reference_image: str | None = None, deadline=None) -> tuple[dict, str | None]:
    remaining_wait = int(deadline - time.monotonic()) if deadline is not None else timeout
    preferred_id = getattr(engine, "current_acc_id", None)
    if kind == "video" and not account_id:
        # Prefer the last account that completed a video; session health alone is insufficient.
        recent = sorted(list(store.tasks.values()), key=lambda task: task.get("created_at", 0), reverse=True)
        for task in recent:
            if task.get("kind") != "video" or task.get("status") != "completed" or not task.get("account"):
                continue
            candidate = store.get_account(task["account"])
            if candidate and candidate.get("enabled", True) and candidate.get("ok") is not False:
                preferred_id = candidate["id"]
                break
    acc = ACCOUNT_POOL.acquire(account_id=account_id,
                               preferred_id=preferred_id,
                               timeout=max(1, remaining_wait))
    cur_acc = acc
    last_exc = None
    try:
        for attempt in range(2):
            if deadline is not None and time.monotonic() >= deadline:
                raise MuseGenerationError("Đã hết thời hạn chờ tổng của tác vụ, dừng thử lại")
            if attempt > 0:
                # Giữ nguyên literal tiếng Trung: chuỗi này cố khớp thông báo lỗi của engine.
                # Lưu ý: hiện nó KHÔNG khớp thông báo thực tế mà engine ném ra, nên nhánh dừng sớm này thực chất không chạy (xem README, mục ghi chú dịch).
                if last_exc and "未产出媒体附件，仅返回了文本回复" in str(last_exc):
                    break
                old_id = cur_acc["id"] if cur_acc else None
                if old_id:
                    ACCOUNT_POOL.release(old_id)
                cur_acc = None
                rem = int(deadline - time.monotonic()) if deadline is not None else timeout
                if rem <= 0:
                    break
                alt = ACCOUNT_POOL.acquire(rotate=True, force_rotate=True, timeout=rem)
                if not alt or (old_id and alt["id"] == old_id):
                    if alt:
                        cur_acc = alt
                    break
                cur_acc = alt
                log.info("【Tự động đổi tài khoản sinh ảnh/video】Chuyển sang tài khoản dự phòng %s (%s) để thử lại...", cur_acc.get("label"), cur_acc["id"])
            try:
                refreshed = _renew_and_persist(cur_acc["id"], wake_vm=True, force=(attempt > 0))
                if refreshed:
                    cur_acc = refreshed
                engine.start()
                remaining = int(deadline - time.monotonic()) if deadline is not None else timeout
                if remaining <= 0:
                    raise MuseGenerationError("Đã hết thời hạn chờ tổng của tác vụ, dừng thử lại")
                res = engine.generate(cur_acc["cookies"], prompt, expect=kind,
                                      timeout=remaining, expires=cur_acc.get("cookies_exp"),
                                      account_id=cur_acc["id"], on_progress=on_progress,
                                      reference_image=reference_image)
                store.mark(cur_acc["id"], True, "")
                _sync_cookies(cur_acc["id"])
                return res, cur_acc["id"]
            except MuseAuthError as exc:
                last_exc = exc
                store.mark(cur_acc["id"], False, str(exc))
                engine.close_current_page()
            except MuseGenerationError as exc:
                last_exc = exc
                store.mark(cur_acc["id"], True, f"Tác vụ bất thường: {str(exc)[:60]}")
                try:
                    engine.reset_thread()
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                engine.close_current_page()
                last_exc = MuseGenerationError(f"Sinh tạo thất bại: {exc}")
        raise last_exc
    finally:
        if cur_acc:
            ACCOUNT_POOL.release(cur_acc.get("id"))
        engine.close_current_page()


# ------------------------- Giao diện cơ bản -------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok", "time": int(time.time())}


@app.get("/readyz")
def readyz():
    accs = [a for a in store.list_accounts() if a.get("enabled", True)]
    return {"status": "ready" if accs else "no_account",
            "accounts": len(accs),
            "browser_running": bool(engine.proc and engine.proc.poll() is None)}


@app.get("/v1/models")
def models(_=Depends(auth)):
    return {"object": "list", "data": MODELS}


# ------------------------- Sinh ảnh -------------------------
def _image_response(req: ImageRequest, res: dict) -> dict:
    item = {"revised_prompt": req.prompt, "url": media_url(res["filename"]),
            "size": req.size or "auto", "kind": res["kind"], "bytes": res["size"]}
    if req.response_format == "b64_json":
        fpath = res.get("path") or os.path.join(CFG.media_dir, res["filename"])
        with open(fpath, "rb") as f:
            item["b64_json"] = base64.b64encode(f.read()).decode()
        item.pop("url", None)
    return {"created": int(time.time()), "data": [item]}


def _queue_image(req: ImageRequest, prompt: str, reference_image: str | None,
                 idempotency_key: str | None = None):
    """Opt-in polling avoids reverse-proxy timeouts; no generation is repeated by polling."""
    if idempotency_key and len(idempotency_key) > 256:
        raise HTTPException(400, "Idempotency-Key too long")
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
    request_hash = hashlib.sha256(json.dumps(
        {"request": req.model_dump(by_alias=True), "reference": reference_image},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with IMAGE_TASK_LOCK:
        tasks = list(store.tasks.values())
        if key_hash:
            for existing in tasks:
                if existing.get("image_request_key") == key_hash:
                    if existing.get("image_request_hash") != request_hash:
                        raise HTTPException(409, "Idempotency-Key reused with different request")
                    return JSONResponse(status_code=202, content={
                        "id": existing["id"], "task_id": existing["id"],
                        "object": "image.task", "status": existing["status"],
                        "progress": existing.get("progress", 0),
                        "created_at": existing["created_at"]})
        # ponytail: one Chromium worker; cap admission instead of adding a broker.
        if sum(t.get("kind") == "image" and t.get("status") in ("queued", "processing")
               for t in tasks) >= 8:
            raise HTTPException(429, "Image queue is full; retry later")
        task = store.create_task("image", req.prompt)
        tid = task["id"]
        store.update_task(tid, api_prompt=prompt, size=req.size,
                          response_format=req.response_format, progress=0,
                          image_request_key=key_hash, image_request_hash=request_hash)

    def worker():
        timing = {"t0": time.time()}
        def _on_acquired():
            timing["t0"] = time.time()
            store.update_task(tid, status="processing", progress=5)
        try:
            res, acc_id = _run_generation(
                prompt, "image", req.timeout or CFG.image_timeout,
                reference_image=reference_image,
                on_progress=lambda p: store.update_task(tid, progress=p),
                on_acquired=_on_acquired)
            # Store only media metadata, not large base64 payloads or reference credentials.
            store.update_task(tid, status="completed", progress=100, account=acc_id,
                              elapsed=round(time.time() - timing["t0"], 1),
                              url=media_url(res["filename"]),
                              result={**{k: res[k] for k in ("filename", "size", "kind")},
                                      "url": media_url(res["filename"])})
        except Exception as exc:  # noqa: BLE001
            store.update_task(tid, status="failed", error=str(exc),
                              elapsed=round(time.time() - timing["t0"], 1))

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(status_code=202, content={
        "id": tid, "task_id": tid, "object": "image.task", "status": "queued",
        "progress": 0, "created_at": task["created_at"]})


@app.post("/v1/images/tasks")
def create_image_task(req: ImageRequest,
                      idempotency_key: str | None = Header(default=None), _=Depends(auth)):
    ref_img = req.reference_image or req.image
    if isinstance(ref_img, dict):
        ref_img = ref_img.get("url") or ref_img.get("b64_json")
    if not ref_img and req.images:
        first = req.images[0]
        ref_img = first.get("image_url") or first.get("url") if isinstance(first, dict) else first
    return _queue_image(req, build_image_prompt(req), ref_img, idempotency_key)


@app.get("/v1/images/tasks/{task_id}")
def get_image_task(task_id: str, _=Depends(auth)):
    task = store.get_task(task_id)
    if not task or task.get("kind") != "image":
        raise HTTPException(404, "image task không tồn tại")
    out = dict(task)
    if out.get("status") == "completed":
        req = ImageRequest(prompt=out["prompt"], size=out.get("size"),
                           response_format=out.get("response_format") or "url")
        out.update(_image_response(req, out["result"]))
    return out


@app.post("/v1/images/generations")
async def images_generations(req: ImageRequest, _=Depends(auth)):
    ref_img = req.reference_image or req.image
    if isinstance(ref_img, dict):
        ref_img = ref_img.get("url") or ref_img.get("b64_json")
    if not ref_img and req.images and isinstance(req.images, list):
        first = req.images[0]
        ref_img = first.get("image_url") or first.get("url") if isinstance(first, dict) else first

    prompt = build_image_prompt(req)
    timeout = req.timeout or CFG.image_timeout
    if req.async_:
        return _queue_image(req, prompt, ref_img)
    try:
        res, _acc = await asyncio.to_thread(_run_generation, prompt, "image", timeout, reference_image=ref_img)
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _image_response(req, res)


@app.post("/v1/images/edits")
async def images_edits(request: Request, _=Depends(auth)):
    """Endpoint sinh ảnh từ ảnh / chỉnh sửa ảnh tương thích OpenAI, hỗ trợ multipart/form-data và application/json."""
    content_type = request.headers.get("content-type", "").lower()
    prompt = ""
    model = "muse-image"
    size = None
    aspect_ratio = None
    response_format = "url"
    timeout = None
    ref_image_data = None
    async_mode = False

    if "multipart/form-data" in content_type:
        form = await request.form()
        async_mode = form.get("async", False)
        prompt = form.get("prompt") or ""
        model = form.get("model") or "muse-image"
        size = form.get("size")
        aspect_ratio = form.get("aspect_ratio")
        response_format = form.get("response_format") or "url"
        timeout_val = form.get("timeout")
        if timeout_val:
            try:
                timeout = int(timeout_val)
            except ValueError:
                pass
        img_field = form.get("image")
        if img_field and hasattr(img_field, "read"):
            content = await img_field.read()
            ref_mime = getattr(img_field, "content_type", "image/png") or "image/png"
            ref_image_data = f"data:{ref_mime};base64,{base64.b64encode(content).decode('ascii')}"
        elif isinstance(img_field, str):
            ref_image_data = img_field
    else:
        body = await request.json()
        async_mode = body.get("async", False)
        prompt = body.get("prompt") or ""
        model = body.get("model") or "muse-image"
        size = body.get("size")
        aspect_ratio = body.get("aspect_ratio")
        response_format = body.get("response_format") or "url"
        timeout = body.get("timeout")
        img_val = body.get("image")
        if isinstance(img_val, dict):
            ref_image_data = img_val.get("url") or img_val.get("b64_json")
        elif isinstance(img_val, str):
            ref_image_data = img_val
        if not ref_image_data and body.get("images") and isinstance(body.get("images"), list):
            first = body["images"][0]
            if isinstance(first, dict):
                ref_image_data = first.get("image_url") or first.get("url")
            elif isinstance(first, str):
                ref_image_data = first
        if not ref_image_data:
            ref_image_data = body.get("reference_image") or body.get("image_url")

    if not prompt:
        prompt = "参考此图片并进行生图创作"  # prompt dự phòng gửi tới muse.ai; giữ nguyên tiếng Trung cho đồng bộ với các payload prompt khác

    req_obj = ImageRequest(
        prompt=prompt,
        model=model,
        size=size,
        aspect_ratio=aspect_ratio,
        response_format=response_format,
        timeout=timeout,
        reference_image=ref_image_data,
        **{"async": async_mode}
    )
    full_prompt = build_image_prompt(req_obj)
    gen_timeout = timeout or CFG.image_timeout
    if req_obj.async_:
        return _queue_image(req_obj, full_prompt, ref_image_data)
    try:
        res, _acc = await asyncio.to_thread(_run_generation, full_prompt, "image", gen_timeout, reference_image=ref_image_data)
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc

    return _image_response(req_obj, res)


# ------------------------- Sinh video (tác vụ bất đồng bộ) -------------------------
@app.post("/v1/videos")
@app.post("/v1/videos/generations")
async def create_video(req: VideoRequest, _=Depends(auth)):
    ref_img = None
    if req.reference_image:
        ref_img = req.reference_image
    elif req.image_url:
        ref_img = req.image_url if isinstance(req.image_url, str) else (req.image_url.get("url") if isinstance(req.image_url, dict) else None)
    elif req.image:
        ref_img = req.image.get("url") if isinstance(req.image, dict) else req.image

    prompt = build_video_prompt(req)
    timeout = req.timeout or CFG.video_timeout
    task = store.create_task("video", req.prompt)
    store.update_task(task["id"], api_prompt=prompt)

    store.update_task(task["id"], progress=10)

    def worker():
        timing = {"t0": time.time()}
        def _on_acquired():
            timing["t0"] = time.time()
            store.update_task(task["id"], status="processing", progress=15)
        try:
            def prog_cb(p):
                store.update_task(task["id"], progress=p)
            res, acc_id = _run_generation(prompt, "video", timeout, on_progress=prog_cb,
                                          reference_image=ref_img, on_acquired=_on_acquired)
            vurl = media_url(res["filename"])
            store.update_task(task["id"], status="completed", progress=100, account=acc_id,
                              elapsed=round(time.time() - timing["t0"], 1),
                              url=vurl,
                              video={"url": vurl},
                              result={"url": vurl,
                                      "filename": res["filename"],
                                      "bytes": res["size"], "kind": res["kind"]})
        except Exception as exc:  # noqa: BLE001
            store.update_task(task["id"], status="failed",
                              elapsed=round(time.time() - timing["t0"], 1), error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return {"id": task["id"], "task_id": task["id"], "object": "video.task", "status": "queued",
            "progress": 10, "created_at": task["created_at"]}


@app.get("/v1/videos/{task_id}")
@app.get("/v1/videos/generations/{task_id}")
def get_video(task_id: str, _=Depends(auth)):
    t = store.get_task(task_id)
    if not t:
        raise HTTPException(404, "task không tồn tại")
    out = dict(t)
    status = out.get("status")
    if status in ("succeeded", "success", "done"):
        out["status"] = "completed"
    if out.get("status") == "completed":
        out["progress"] = 100
    elif out.get("status") == "processing":
        elapsed = time.time() - out.get("created_at", time.time())
        calc_prog = min(92, int(20 + elapsed * 1.1))
        out["progress"] = max(out.get("progress", 0) or 0, calc_prog)
    vurl = out.get("url")
    if not vurl and isinstance(out.get("result"), dict):
        vurl = out["result"].get("url")
    if vurl:
        out["url"] = vurl
        if "video" not in out:
            out["video"] = {"url": vurl}
    return out


# ------------------------- Hội thoại (tương thích OpenAI) -------------------------
def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _chat_chunk(cid: str, created: int, model: str, delta: dict,
                finish: str | None = None) -> dict:
    return {"id": cid, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _want_usage(stream_options) -> bool:
    """Hạ nguồn (LangChain, một số SDK / framework agent) sẽ gửi kèm
    `stream_options.include_usage=true`, yêu cầu bổ sung trước [DONE] một
    phân đoạn `choices: []` + `usage`. Không gửi thì một số framework sẽ chờ usage mãi và treo."""
    if isinstance(stream_options, dict):
        return bool(stream_options.get("include_usage"))
    return False


def _pace_text(text: str, chunk_size: int = 2, delay: float = 0.012):
    """Chia nhỏ mượt văn bản lớn thành các Token vi-stream giống máy đánh chữ của LLM gốc.
    Nếu văn bản truyền vào vốn nhỏ (<= 3 ký tự), cho qua ngay tức thì, không độ trễ;
    nếu văn bản truyền vào là khối lớn (như DOM cập nhật hàng loạt), xuất mượt theo từng chunk_size ký tự với độ trễ delay.
    """
    if not text:
        return
    if len(text) <= 3 or delay <= 0:
        yield text
        return
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]
        if i + chunk_size < len(text):
            time.sleep(delay)


_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, _=Depends(auth)):
    """Giao diện hội thoại tương thích OpenAI —— đủ loại client agent đều kết nối được.

    Lưu ý: trang muse.ai là agent **tự định tuyến**, trên giao diện không có bộ chọn model, mọi yêu cầu
    đều rơi vào cùng một trợ lý web (tự xưng Koda, bên dưới là dòng model ngôn ngữ Muse). Nên
    trường `model` chỉ dùng để tương thích hạ nguồn, không ảnh hưởng kết quả định tuyến.

    Khi yêu cầu mang `tools`, sẽ nhồi【giao thức gọi công cụ】và phân tích JSON do model xuất ra trở lại
    `tool_calls` (muse.ai không có function calling gốc, lớp này thuộc thích ứng giao thức).

    Hội thoại và sinh ảnh/sinh video dùng chung một instance trình duyệt, nối tiếp nhau nhờ `GEN_LOCK`.
    """
    prompt = build_chat_prompt(req.messages) or (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "messages rỗng")

    # Giao thức gọi công cụ mặc định không nhồi —— thực đo cho thấy trợ lý muse.ai từ chối thẳng việc xuất "gọi công cụ giả",
    # nhồi vào còn làm hỏng câu trả lời bình thường; xem chú thích tool_protocol trong config.py.
    tool_note = build_tools_prompt(req.tools) if CFG.tool_protocol else ""
    if tool_note:
        # Đặt ở cuối: đầu là system prompt của agent, kẹp giữa dễ bị bỏ qua;
        # sát ngay trước tin nhắn người dùng, mức độ tuân thủ chỉ thị ở cuối của model cao hơn rõ rệt.
        prompt = prompt + "\n\n" + tool_note

    model = resolve_model(req.model, default="muse-spark")
    timeout = int(req.timeout or CFG.chat_timeout)
    acc = store.pick_account(rotate=True, preferred_id=getattr(engine, "current_acc_id", None))
    if not acc:
        raise HTTPException(400, "Không có tài khoản khả dụng, hãy nhập cookie trong trang quản trị trước")

    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    acc_id = acc["id"]
    cookies = acc["cookies"]
    expires = acc.get("cookies_exp")

    def tool_call_deltas(calls: list[dict]) -> list[list[dict]]:
        """Gửi thành hai phân đoạn theo thói quen OpenAI: trước là id/name, sau là toàn văn arguments."""
        head = [{"index": i, "id": c["id"], "type": "function",
                 "function": {"name": c["function"]["name"], "arguments": ""}}
                for i, c in enumerate(calls)]
        body = [{"index": i, "function": {"arguments": c["function"]["arguments"]}}
                for i, c in enumerate(calls)]
        return [head, body]

    if req.stream:
        def sync_stream():
            try:
                # Gửi ngay gói đầu role: assistant khoảnh khắc bắt tay thiết lập xong, để client hạ nguồn bắt được con trỏ tức thì
                yield _sse(_chat_chunk(cid, created, model, {"role": "assistant"}))

                stream_gen = safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id)
                if not tool_note:
                    for chunk in stream_gen:
                        for piece in _pace_text(chunk):
                            yield _sse(_chat_chunk(cid, created, model, {"content": piece}))
                    yield _sse(_chat_chunk(cid, created, model, {}, finish="stop"))
                else:
                    buf, mode = "", None
                    for chunk in stream_gen:
                        if mode == "text":
                            for piece in _pace_text(chunk):
                                yield _sse(_chat_chunk(cid, created, model, {"content": piece}))
                            continue
                        buf += chunk
                        if mode is None:
                            head = buf.lstrip()
                            if len(head) >= 2:
                                if head[0] in "{[" or head.startswith("```"):
                                    mode = "maybe_tool"
                                else:
                                    mode = "text"
                                    for piece in _pace_text(buf):
                                        yield _sse(_chat_chunk(cid, created, model, {"content": piece}))
                                    buf = ""
                    if mode == "text":
                        yield _sse(_chat_chunk(cid, created, model, {}, finish="stop"))
                    else:
                        calls, rest = parse_tool_calls(buf)
                        if calls:
                            if rest:
                                for piece in _pace_text(rest):
                                    yield _sse(_chat_chunk(cid, created, model, {"content": piece}))
                            for piece in tool_call_deltas(calls):
                                yield _sse(_chat_chunk(cid, created, model, {"tool_calls": piece}))
                            yield _sse(_chat_chunk(cid, created, model, {}, finish="tool_calls"))
                        else:
                            if buf:
                                for piece in _pace_text(buf):
                                    yield _sse(_chat_chunk(cid, created, model, {"content": piece}))
                            yield _sse(_chat_chunk(cid, created, model, {}, finish="stop"))
                if _want_usage(req.stream_options):
                    yield _sse({"id": cid, "object": "chat.completion.chunk",
                                "created": created, "model": model,
                                "choices": [],
                                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
                yield "data: [DONE]\n\n"
                store.mark(acc_id, True, "")
            except MuseAuthError as exc:
                store.mark(acc_id, False, str(exc))
                yield _sse({"error": {"message": str(exc), "type": "auth_error", "code": 401}})
            except MuseGenerationError as exc:
                store.mark(acc_id, True, f"Trợ lý quá hạn: {str(exc)[:60]}")
                try:
                    engine.reset_thread()
                except Exception:
                    pass
                yield _sse({"error": {"message": str(exc), "type": "server_error", "code": 502}})
            except Exception as exc:
                yield _sse({"error": {"message": f"Lỗi nội bộ: {exc}", "type": "server_error", "code": 500}})
        return StreamingResponse(sync_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

    def run() -> str:
        return "".join(safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id))

    try:
        text = await asyncio.to_thread(run)
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc))
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc))

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if tool_note:
        calls, rest = parse_tool_calls(text)
        if calls:
            return {"id": cid, "object": "chat.completion", "created": created,
                    "model": model,
                    "choices": [{"index": 0, "finish_reason": "tool_calls",
                                 "message": {"role": "assistant",
                                             "content": rest or None,
                                             "tool_calls": calls}}],
                    "usage": usage}
    return {"id": cid, "object": "chat.completion", "created": created,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": usage}


def _responses_messages(req: ResponsesRequest) -> list[ChatMessage]:
    """Chuẩn hóa instructions / input của Responses API thành messages."""
    msgs: list[ChatMessage] = []
    if req.instructions:
        msgs.append(ChatMessage(role="system", content=req.instructions))
    inp = req.input
    if isinstance(inp, str):
        msgs.append(ChatMessage(role="user", content=inp))
        return msgs
    if isinstance(inp, list):
        for item in inp:
            if isinstance(item, str):
                msgs.append(ChatMessage(role="user", content=item))
                continue
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if item.get("role"):                      # dạng message
                msgs.append(ChatMessage(role=item["role"],
                                        content=item.get("content")))
            elif itype == "input_text":
                msgs.append(ChatMessage(role="user", content=item.get("text")))
            elif itype == "function_call_output":
                msgs.append(ChatMessage(role="user", content="【工具执行结果】\n"
                                        + str(item.get("output") or "")))
            elif itype == "function_call":
                msgs.append(ChatMessage(role="assistant", content="【请求调用工具】"
                                        + str(item.get("name") or "")))
    return msgs


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/v1/responses")
async def responses_api(req: ResponsesRequest, _=Depends(auth)):
    """OpenAI Responses API —— Codex bản mới mặc định đi qua endpoint này.

    Chỉ hiện thực tập con mà Codex thực sự dùng: văn bản vào → văn bản ra (kèm sự kiện streaming).
    muse.ai không trả về tool_calls có cấu trúc, về ranh giới gọi công cụ xem README.
    """
    prompt = build_chat_prompt(_responses_messages(req))
    if not prompt:
        raise HTTPException(400, "input rỗng")

    model = resolve_model(req.model, default="muse-spark")
    timeout = int(req.timeout or CFG.chat_timeout)
    acc = store.pick_account(rotate=True, preferred_id=getattr(engine, "current_acc_id", None))
    if not acc:
        raise HTTPException(400, "Không có tài khoản khả dụng, hãy nhập cookie trong trang quản trị trước")

    rid = "resp_" + uuid.uuid4().hex[:24]
    mid = "msg_" + uuid.uuid4().hex[:24]
    created = int(time.time())
    acc_id, cookies, expires = acc["id"], acc["cookies"], acc.get("cookies_exp")

    def envelope(status: str, text: str = "") -> dict:
        done = status == "completed"
        return {"id": rid, "object": "response", "created_at": created,
                "status": status, "model": model,
                "output": [{"type": "message", "id": mid, "role": "assistant",
                            "status": "completed" if done else "in_progress",
                            "content": ([{"type": "output_text", "text": text,
                                          "annotations": []}] if text else [])}],
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "total_tokens": 0}}

    if req.stream:
        def sync_stream():
            try:
                yield _sse_event("response.created", {
                    "type": "response.created",
                    "response": envelope("in_progress")})
                yield _sse_event("response.output_item.added", {
                    "type": "response.output_item.added", "output_index": 0,
                    "item": {"id": mid, "type": "message", "role": "assistant",
                             "status": "in_progress", "content": []}})
                yield _sse_event("response.content_part.added", {
                    "type": "response.content_part.added", "item_id": mid,
                    "output_index": 0, "content_index": 0,
                    "part": {"type": "output_text", "text": "",
                             "annotations": []}})
                full = ""
                for chunk in safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id):
                    full += chunk
                    yield _sse_event("response.output_text.delta", {
                        "type": "response.output_text.delta", "item_id": mid,
                        "output_index": 0, "content_index": 0, "delta": chunk})
                yield _sse_event("response.output_text.done", {
                    "type": "response.output_text.done", "item_id": mid,
                    "output_index": 0, "content_index": 0, "text": full})
                yield _sse_event("response.output_item.done", {
                    "type": "response.output_item.done", "output_index": 0,
                    "item": {"id": mid, "type": "message", "role": "assistant",
                             "status": "completed",
                             "content": [{"type": "output_text", "text": full,
                                          "annotations": []}]}})
                yield _sse_event("response.completed", {
                    "type": "response.completed",
                    "response": envelope("completed", full)})
            except Exception as exc:  # noqa: BLE001
                log.warning("luồng responses thất bại: %s", exc)
                yield _sse_event("response.failed", {
                    "type": "response.failed",
                    "response": envelope("failed")})
        return StreamingResponse(sync_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

    def run() -> str:
        return "".join(safe_chat_stream(cookies, prompt, expires, timeout, account_id=acc_id))

    try:
        text = await asyncio.to_thread(run)
    except MuseAuthError as exc:
        raise HTTPException(401, str(exc))
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc))

    return envelope("completed", text)


@app.get("/v1/media/{name}")
def get_media(name: str):
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Tên tệp không hợp lệ")
    p = os.path.join(CFG.media_dir, name)
    if not os.path.isfile(p):
        raise HTTPException(404, "Tệp không tồn tại")
    return FileResponse(p)


# ------------------------- Quản trị: Tổng quan -------------------------
@app.get("/admin/status")
def admin_status(_=Depends(auth)):
    base = _public_base()
    return {
        "accounts": store.list_accounts(),
        "stats": store.stats(),
        "tasks": store.list_tasks(20),
        "media_count": len(os.listdir(CFG.media_dir))
        if os.path.isdir(CFG.media_dir) else 0,
        "browser_running": bool(engine.proc and engine.proc.poll() is None),
        "essential_cookies": list(ESSENTIAL_COOKIES),
        "base_url": f"{base}/v1" if base else "",
        "config": {"site": CFG.site_url, "cdp_port": CFG.cdp_port,
                   "image_timeout": CFG.image_timeout,
                   "video_timeout": CFG.video_timeout,
                   "host": CFG.host, "port": CFG.port,
                   "public_base": base},
    }


# ------------------------- Quản trị: Bể tài khoản -------------------------
@app.get("/admin/accounts")
def list_accounts(_=Depends(auth)):
    return {"accounts": store.list_accounts(), "stats": store.stats()}


@app.post("/admin/accounts")
def add_account(req: AccountRequest, _=Depends(auth)):
    added: list[dict] = []
    seen: list[dict] = []          # toàn bộ cookie nhập trong lần này, dùng để kiểm tra các mục cốt lõi đã đủ chưa

    if req.batch:
        for label, cookies in parse_batch(req.batch):
            acc = store.add_account(cookies, label)
            seen.append(cookies)
            added.append({"id": acc["id"], "label": acc["label"],
                          "cookie_count": len(cookies),
                          "expires_at": acc.get("expires_at")})

    cookies = dict(req.cookies)
    if req.cookie_header:
        cookies.update(parse_cookie_text(req.cookie_header))
    if cookies:
        acc = store.add_account(cookies, req.label, cookies_exp=req.expires)
        seen.append(cookies)
        added.append({"id": acc["id"], "label": acc["label"],
                      "cookie_count": len(cookies),
                      "expires_at": acc.get("expires_at")})

    if not added:
        raise HTTPException(400, "Không phân tích được cookie nào, hãy kiểm tra định dạng")

    # Chỉ cần một tài khoản gom đủ cookie cốt lõi là đạt (khi nhập hàng loạt thì xét theo tổng thể)
    missing = [n for n in ESSENTIAL_COOKIES
               if not any(n in c for c in seen)]
    return {"added": added, "count": len(added),
            "essential_missing": missing,
            "warning": (f"Thiếu cookie cốt lõi: {', '.join(missing)}, tài khoản này có thể không sinh tạo được"
                        if missing else "")}


@app.patch("/admin/accounts/{aid}")
def patch_account(aid: str, req: AccountPatch, _=Depends(auth)):
    acc = store.update_account(aid, label=req.label, enabled=req.enabled)
    if not acc:
        raise HTTPException(404, "Tài khoản không tồn tại")
    return {k: v for k, v in acc.items() if k != "cookies"} | {
        "cookie_count": len(acc.get("cookies", {}))}


@app.delete("/admin/accounts/{aid}")
def del_account(aid: str, _=Depends(auth)):
    ok = store.delete_account(aid)
    if not ok:
        raise HTTPException(404, "Tài khoản không tồn tại")
    return {"deleted": True, "id": aid}


@app.post("/admin/accounts/{aid}/test")
async def test_account(aid: str, _=Depends(auth)):
    """Mở muse.ai thật để kiểm tra cookie của tài khoản này còn đăng nhập được không."""
    acc = store.get_account(aid)
    if not acc:
        raise HTTPException(404, "Tài khoản không tồn tại")
    if not acc.get("cookies"):
        raise HTTPException(400, "Tài khoản này không có cookie")

    def _probe():
        with GEN_LOCK:
            try:
                engine.start()
                engine.refresh(acc["cookies"], acc.get("cookies_exp"))
                synced = _sync_cookies(aid)
                quota = None
                quota_error = None
                try:  # Session validity and quota readability are independent results.
                    quota = engine.quota(acc["cookies"],
                                         acc.get("cookies_exp"), account_id=aid)
                    if quota.get("found"):
                        quota["checked_at"] = int(time.time())
                        store.update_account(aid, quota=quota)
                    else:
                        quota_error = "Bảng hạn mức chưa đọc được dữ liệu hợp lệ; bộ nhớ đệm cũ không bị ghi đè"
                        quota = None
                except Exception as exc:  # noqa: BLE001
                    quota_error = f"{type(exc).__name__}; bộ nhớ đệm hạn mức chưa cập nhật"
                store.mark(aid, True, "Phiên hợp lệ" + ("; đọc hạn mức thất bại" if quota_error else ""))
                return {"ok": True, "message": "Phiên hợp lệ, có thể sinh tạo bình thường",
                        "synced": synced, "quota": quota,
                        "quota_error": quota_error}
            except MuseAuthError as exc:
                store.mark(aid, False, str(exc)[:200])
                return {"ok": False, "message": str(exc)[:200]}
            except Exception as exc:  # noqa: BLE001
                store.touch_keepalive(aid, None, f"Kiểm tra chưa xác nhận (giữ nguyên trạng thái tài khoản): {str(exc)[:200]}")
                return {"ok": False, "message": str(exc)[:200]}

    res = await asyncio.to_thread(_probe)
    if not res["ok"]:
        return JSONResponse(res, status_code=200)
    return res


@app.post("/admin/accounts/{aid}/relogin")
async def relogin_account(aid: str, _=Depends(auth)):
    return await test_account(aid, _)


@app.post("/admin/accounts/{aid}/cookies")
def update_cookies(aid: str, payload: dict = Body(...), _=Depends(auth)):
    """Cập nhật cookie của một tài khoản (dùng để bổ sung cookie mới sau khi phiên hết hạn)."""
    cookies = dict(payload.get("cookies") or {})
    if payload.get("cookie_header"):
        cookies.update(parse_cookie_text(payload["cookie_header"]))
    if not cookies:
        raise HTTPException(400, "Không phân tích được cookie")
    exp = {k: int(v) for k, v in (payload.get("expires") or {}).items()
           if _pos(v)}
    # Cookie bổ sung thuộc "phiên hoàn toàn mới", nên mốc neo ước lượng thời hạn phải đặt lại về hiện tại,
    # nếu không sẽ kế thừa mốc neo của phiên cũ, khiến số ngày còn lại bị tính thiếu.
    acc = store.update_account(aid, cookies=cookies, ok=None,
                               note="Đã cập nhật cookie",
                               expiry_anchor=int(time.time()),
                               cookies_exp=exp or None)
    if not acc:
        raise HTTPException(404, "Tài khoản không tồn tại")
    return {"ok": True, "id": aid, "cookie_count": len(cookies),
            "expires_at": acc.get("expires_at")}


@app.post("/admin/relogin")
def relogin(_=Depends(auth)):
    acc = store.pick_account(rotate=True)
    if not acc:
        raise HTTPException(400, "Không có tài khoản khả dụng")
    try:
        engine.start()
        engine.refresh(acc["cookies"], acc.get("cookies_exp"))
        return {"ok": True, "account": acc["id"]}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


# ------------------------- Quản trị: Hạn mức -------------------------
@app.post("/admin/accounts/{aid}/quota")
async def query_quota(aid: str, _=Depends(auth)):
    """Mở bảng Settings của muse.ai để đọc hạn mức tài khoản này (thời gian thực), rồi lưu vào bản ghi tài khoản.

    Ví dụ trả về: {"plan":"Free plan","weekly_reset":"Sep 30",
             "weekly_used_pct":1,"extra_left":"2B tokens left",
             "extra_used_pct":0,"extra_expires":"never"}
    """
    acc = store.get_account(aid)
    if not acc:
        raise HTTPException(404, "Tài khoản không tồn tại")
    if not acc.get("cookies"):
        raise HTTPException(400, "Tài khoản này không có cookie")

    def _probe():
        with GEN_LOCK:
            engine.start()
            q = engine.quota(acc["cookies"], acc.get("cookies_exp"), account_id=aid)
            if not q.get("found"):
                raise MuseGenerationError("Bảng hạn mức chưa đọc được dữ liệu hợp lệ; bộ nhớ đệm cũ không bị ghi đè")
            q["checked_at"] = int(time.time())
            store.update_account(aid, quota=q)
            return q

    try:
        return await asyncio.to_thread(_probe)
    except MuseAuthError as exc:
        store.mark(aid, False, str(exc)[:200])
        raise HTTPException(401, str(exc)) from exc
    except MuseGenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Truy vấn hạn mức thất bại: {exc}") from exc


@app.post("/admin/quota")
async def query_any_quota(_=Depends(auth)):
    """Dùng tài khoản khả dụng lâu chưa dùng nhất để tra hạn mức một lần (các tài khoản cùng bể thường
    chia sẻ cùng một gói muse.ai, chỉ đủ khi có một người dùng; nhiều tài khoản thì nên tra theo từng tài khoản)."""
    acc = store.pick_account(rotate=True)
    if not acc:
        raise HTTPException(400, "Không có tài khoản khả dụng")
    return await query_quota(acc["id"], _)


# ------------------------- Quản trị: Thông tin kết nối / API Key -------------------------
def _public_base() -> str:
    return (CFG.public_base or "").rstrip("/")


@app.get("/admin/apikey")
def get_apikey(_=Depends(auth)):
    base = _public_base()
    base_url = f"{base}/v1" if base else ""
    return {"api_key": CFG.api_key, "base_url": base_url,
            "models_url": f"{base}/v1/models" if base else "",
            "media_url": f"{base}/v1/media/{{name}}" if base else "/v1/media/{name}"}


@app.post("/admin/apikey/rotate")
def rotate_apikey(_=Depends(auth)):
    """Tạo API Key mới, ghi vào .env và có hiệu lực ngay (không cần khởi động lại)."""
    import secrets
    new_key = "m2a_" + secrets.token_hex(24)
    old = CFG.api_key
    CFG.api_key = new_key
    _persist_env("MUSE2API_KEY", new_key)
    return {"ok": True, "api_key": new_key, "previous": old,
            "message": "Đã tạo Key mới và có hiệu lực ngay; Key cũ đã hết hiệu lực, hãy cập nhật dự án phía sau"}


def _persist_env(key: str, value: str):
    """Ghi cấu hình trở lại .env (giữ nguyên các dòng khác, thay thế nguyên tử)."""
    path = os.path.join(CFG.base_dir, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    os.replace(tmp, path)


# ------------------------- Quản trị: Lấy Cookie -------------------------
@app.get("/admin/cookie-helper")
def cookie_helper(download: int = 0):
    """Trả về script trợ giúp lấy cookie chạy trên máy (cách nâng cao, cần cài Python)."""
    p = os.path.join(BASE_DIR, "tools", "get_muse_cookie.py")
    if not os.path.isfile(p):
        raise HTTPException(404, "Thiếu script trợ giúp")
    headers = {}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="get_muse_cookie.py"'
    return FileResponse(p, media_type="text/x-python", headers=headers)


@app.get("/admin/extension")
def extension_zip():
    """Đóng gói tiện ích trình duyệt thành zip để trả về (cách khuyến nghị, không cần dòng lệnh).

    Người dùng tải về rồi giải nén → vào chrome://extensions bật chế độ nhà phát triển để nạp → bấm một cái là nhập cookie.
    """
    src = os.path.join(BASE_DIR, "extension")
    if not os.path.isdir(src):
        raise HTTPException(404, "Thiếu thư mục tiện ích")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(src)):
            p = os.path.join(src, name)
            if os.path.isfile(p):
                z.write(p, os.path.join("muse2api-extension", name))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 'attachment; filename="muse2api-extension.zip"',
                 "Cache-Control": "no-store"})


@app.get("/admin/extension/files")
def extension_files():
    """Liệt kê nội dung thư mục tiện ích (dùng cho phần hiển thị giao diện)."""
    src = os.path.join(BASE_DIR, "extension")
    if not os.path.isdir(src):
        raise HTTPException(404, "Thiếu thư mục tiện ích")
    return {"files": sorted(f for f in os.listdir(src)
                            if os.path.isfile(os.path.join(src, f)))}


# ------------------------- Quản trị: Tác vụ / Media -------------------------
@app.get("/admin/tasks")
def admin_tasks(limit: int = 50, _=Depends(auth)):
    return {"tasks": store.list_tasks(limit)}


@app.delete("/admin/tasks/{tid}")
def del_task(tid: str, _=Depends(auth)):
    if not store.delete_task(tid):
        raise HTTPException(404, "Tác vụ không tồn tại")
    return {"deleted": True}


@app.post("/admin/tasks/clear")
def clear_tasks(payload: dict = Body(default={}), _=Depends(auth)):
    return {"removed": store.clear_tasks(int(payload.get("keep") or 0))}


@app.get("/admin/media")
def admin_media(_=Depends(auth)):
    d = CFG.media_dir
    items = []
    if os.path.isdir(d):
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(name)[1].lower()
            items.append({"name": name, "url": media_url(name), "bytes": os.path.getsize(p),
                          "mtime": int(os.path.getmtime(p)),
                          "kind": "video" if ext in (".mp4", ".webm", ".mov") else "image"})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"media": items, "count": len(items)}


@app.post("/admin/media/delete")
def delete_media(payload: dict = Body(default={}), _=Depends(auth)):
    names = payload.get("names", [])
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        raise HTTPException(400, "names phải là mảng tên tệp")
    d = CFG.media_dir
    removed = 0
    errors = []
    for name in names:
        if not isinstance(name, str) or "/" in name or "\\" in name or ".." in name:
            errors.append(f"Tên tệp không hợp lệ: {name}")
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
                removed += 1
            except OSError as e:
                errors.append(f"{name}: {str(e)}")
    return {"removed": removed, "errors": errors}


@app.delete("/admin/media/{name}")
def delete_single_media(name: str, _=Depends(auth)):
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Tên tệp không hợp lệ")
    p = os.path.join(CFG.media_dir, name)
    if not os.path.isfile(p):
        raise HTTPException(404, "Tệp không tồn tại")
    try:
        os.remove(p)
        return {"status": "ok", "deleted": name}
    except OSError as e:
        raise HTTPException(500, f"Xóa thất bại: {e}")


# ------------------------- Trang giao diện -------------------------
def _read_page(filename: str) -> str | None:
    """Đọc một tệp HTML trong BASE_DIR; trả None nếu thiếu/không đọc được."""
    p = os.path.join(BASE_DIR, filename)
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _admin_html() -> str:
    """Giao diện cổ điển (bản 1:1 với kho gốc) — admin.html."""
    html = _read_page("admin.html")
    if html is not None:
        return html
    return ("<!doctype html><meta charset=utf-8><body style='background:#0b0f19;"
            "color:#e6e8ee;font-family:system-ui;padding:40px'>"
            "<h2>muse2api</h2><p>Thiếu tệp trang quản trị admin.html.</p>"
            "<p>Các endpoint khả dụng: <code>/v1/images/generations</code>, "
            "<code>/v1/videos</code></p></body>")


def _admin_v2_html() -> str:
    """Giao diện mới theo phong cách form — admin-v2.html.

    Nếu thiếu tệp thì tự lùi về giao diện cổ điển để dịch vụ không bị gián đoạn.
    """
    return _read_page("admin-v2.html") or _admin_html()


@app.get("/", response_class=HTMLResponse)
def index():
    return _admin_v2_html()


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return _admin_v2_html()


@app.get("/admin/v2", response_class=HTMLResponse)
def admin_page_v2():
    """Alias tường minh của giao diện mới."""
    return _admin_v2_html()


@app.get("/admin/classic", response_class=HTMLResponse)
def admin_page_classic():
    """Giao diện cổ điển, giữ nguyên 1:1 với kho gốc."""
    return _admin_html()



# ------------------------- Tự động giữ sống tài khoản và gia hạn âm thầm -------------------------
KEEPALIVE_LOCK = asyncio.Lock()
KEEPALIVE_STATE = {
    "running": False,
    "last_run": None,
    "last_result": None,
    "next_run": None,
}


def _probe_account_sync(aid: str, check_quota: bool = False) -> dict:
    """Gọi muse.ai/api/session để kích hoạt cổng Meta cấp hatch_vml mới (+48h) / hatch_sess (+30d) và đánh thức VM trên cloud."""
    acc = store.get_account(aid)
    if not acc or not acc.get("cookies"):
        return {"ok": False, "id": aid, "label": (acc or {}).get("label", aid), "error": "Tài khoản không có cookie hợp lệ"}
    try:
        res = engine.renew_session_http(acc["cookies"], acc.get("cookies_exp"), wake_vm=True)
        store.update_account(
            aid,
            cookies=res["cookies"],
            cookies_exp=res["cookies_exp"],
            ok=True if res.get("ok") else False,
            synced_at=int(time.time()),
        )
        vm_state = res.get("vm_state") or "RUNNING"
        store.touch_keepalive(aid, True, f"Phiên hợp lệ · tự động giữ sống (VM: {vm_state})")
        quota = acc.get("quota")
        if check_quota and GEN_LOCK.acquire(blocking=False):
            try:
                engine.start()
                quota = engine.quota(res["cookies"], res["cookies_exp"],
                                     account_id=aid)
                if not quota.get("found"):
                    raise MuseGenerationError("Bảng hạn mức chưa đọc được dữ liệu hợp lệ; bộ nhớ đệm cũ không bị ghi đè")
                quota["checked_at"] = int(time.time())
                store.update_account(aid, quota=quota)
            except Exception as qe:  # noqa: BLE001
                log.warning("Đọc hạn mức tài khoản %s thất bại: %s", aid, qe)
            finally:
                GEN_LOCK.release()
        updated = store.get_account(aid) or {}
        return {
            "ok": True,
            "id": aid,
            "label": acc.get("label", aid),
            "vm_id": res.get("vm_id"),
            "vm_state": vm_state,
            "wake_ok": res.get("wake_ok"),
            "expires_at": updated.get("expires_at"),
            "quota": quota,
        }
    except MuseAuthError as exc:
        store.touch_keepalive(aid, False, f"Giữ sống xác thực thất bại: {str(exc)[:200]}")
        return {"ok": False, "id": aid, "label": acc.get("label", aid), "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        store.touch_keepalive(aid, None, f"Giữ sống chưa xác nhận (giữ nguyên trạng thái tài khoản): {str(exc)[:200]}")
        return {"ok": False, "id": aid, "label": acc.get("label", aid), "error": str(exc)}


async def run_keepalive_all(force: bool = False) -> dict:
    """Thực thi giữ sống tài khoản: force=True thì cưỡng bức làm mới mọi tài khoản đang bật, nếu không thì làm mới tài khoản sắp hết hạn/chưa kiểm tra và giữ VM ở trạng thái nóng."""
    async with KEEPALIVE_LOCK:
        now = int(time.time())
        KEEPALIVE_STATE["running"] = True
        KEEPALIVE_STATE["last_run"] = now
        results = []
        skipped = []

        try:
            include_disabled = getattr(CFG, "keepalive_disabled_accounts", False)
            accounts = [a for a in store.list_accounts() if include_disabled or a.get("enabled", True)]
            for a in accounts:
                aid = a["id"]
                label = a.get("label", aid)
                exp_at = a.get("expires_at")
                last_ka = a.get("last_keepalive") or 0

                needs_run = (
                    force
                    or not last_ka
                    or ((now - last_ka) > 6 * 3600 and
                        (not exp_at or exp_at - now < 36 * 3600 or
                         a.get("ok") is not True))
                )
                if not needs_run:
                    skipped.append({"id": aid, "label": label, "reason": "Phiên còn dư dả và mới giữ sống gần đây"})
                    continue

                log.info("【Tự động giữ sống】Đang gia hạn âm thầm và đánh thức VM cho tài khoản %s (%s)...", label, aid)
                res = await asyncio.to_thread(_probe_account_sync, aid, False)
                results.append(res)
                log.info("【Tự động giữ sống】Kết quả tài khoản %s: ok=%s expires_at=%s", label, res.get("ok"), res.get("expires_at"))
                await asyncio.sleep(0.5)

            summary = {
                "checked_at": now,
                "refreshed_count": len(results),
                "refreshed": results,
                "skipped_count": len(skipped),
                "skipped": skipped,
            }
            KEEPALIVE_STATE["last_result"] = summary
            KEEPALIVE_STATE["next_run"] = now + 900
            return summary
        finally:
            KEEPALIVE_STATE["running"] = False


def _warmup_browser_sync():
    """Âm thầm làm nóng trình duyệt và đường hầm WebSocket của tài khoản khả dụng đầu tiên ở nền, để sau khi khởi động lại yêu cầu đầu tiên cũng phản hồi tức thì."""
    if getattr(engine, "current_acc_id", None) and engine.page is not None:
        return
    if not GEN_LOCK.acquire(blocking=False):
        return
    acc = None
    try:
        acc = ACCOUNT_POOL.acquire(rotate=False, timeout=0.1, record_use=False)
        log.info("【Làm nóng trình duyệt】Đang làm nóng tab dự phòng của tài khoản %s (%s) ở nền...", acc.get("label"), acc["id"])
        refreshed = _renew_and_persist(acc["id"], wake_vm=True, force=False) or acc
        engine.start()
        engine.ensure_page(refreshed["cookies"], refreshed.get("cookies_exp"), account_id=acc["id"])
        log.info("【Làm nóng trình duyệt】Tab dự phòng và WebSocket của tài khoản %s (%s) đã sẵn sàng", acc.get("label"), acc["id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("【Làm nóng trình duyệt】Làm nóng gặp lỗi: %s", exc)
    finally:
        # Warmup runs in a worker thread; do not retain its account reservation or page.
        engine.close_current_page()
        if acc:
            ACCOUNT_POOL.release(acc["id"])
        GEN_LOCK.release()


async def _keepalive_loop():
    """Tác vụ nền thường trú: mỗi 15 phút thăm dò một lần tình trạng tài khoản và giữ VM ở trạng thái nóng."""
    log.info("【Tiến trình giữ sống tự động】Đã khởi động, chu kỳ kiểm tra: 15 phút")
    await asyncio.sleep(1)
    try:
        await asyncio.to_thread(_warmup_browser_sync)
    except Exception as e:  # noqa: BLE001
        log.warning("【Làm nóng trình duyệt】Gặp lỗi: %s", e)
    while True:
        try:
            await run_keepalive_all(force=False)
        except Exception as e:  # noqa: BLE001
            log.error("【Tiến trình giữ sống tự động】Thăm dò gặp lỗi: %s", e)
        await asyncio.sleep(900)


@app.post("/admin/accounts/keepalive")
async def trigger_keepalive_all(force: bool = True, _=Depends(auth)):
    """Quản trị viên kích hoạt thủ công một lần giữ sống gia hạn toàn bộ tài khoản."""
    if KEEPALIVE_STATE["running"]:
        return {"status": "busy", "message": "Tác vụ giữ sống đang chạy, vui lòng đợi"}
    return await run_keepalive_all(force=force)


@app.post("/admin/accounts/{aid}/keepalive")
async def trigger_keepalive_single(aid: str, _=Depends(auth)):
    """Thực thi giữ sống gia hạn thủ công cho một tài khoản."""
    return await asyncio.to_thread(_probe_account_sync, aid, False)


@app.get("/admin/keepalive/status")
def get_keepalive_status(_=Depends(auth)):
    """Lấy trạng thái coroutine giữ sống."""
    return KEEPALIVE_STATE


# ------------------------- Phát hiện cập nhật kho thời gian thực, thông báo và nâng cấp trực tuyến một chạm -------------------------
# Kho đích của tính năng tự cập nhật. Đổi hai hằng dưới đây nếu fork sang tài khoản khác.
REPO_OWNER = "lionels-kayden"
REPO_NAME = "muse2api-lio"
REPO_SLUG = f"{REPO_OWNER}/{REPO_NAME}"
REPO_URL = f"https://github.com/{REPO_SLUG}"
# Kho gốc để tham chiếu (nguồn gốc dự án). KHÔNG dùng cho tự cập nhật, vì kho gốc là bản
# tiếng Trung và sẽ ghi đè bản Việt hoá này.
UPSTREAM_SLUG = "czg86389-hub/muse2api"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_SLUG}"
TRACKED_REPO_PATHS = [
    "app.py", "engine.py", "store.py", "cdp.py", "config.py",
    "admin.html", "admin-v2.html", "README.md", "version.json", "requirements.txt",
    "Dockerfile", "docker-compose.yml", ".env.example", ".gitignore",
    "LICENSE", "extension", "deploy", "tools",
]
_UPDATE_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


def _read_env_key(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    path = os.path.join(CFG.base_dir, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f.read().splitlines():
                s = ln.strip()
                if s.startswith(key + "="):
                    return s.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _read_local_version() -> dict:
    p = os.path.join(BASE_DIR, "version.json")
    try:
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
            if isinstance(obj, dict):
                return obj
    except Exception:
        pass
    return {"version": "1.5.0", "highlights": []}


def _installed_sha_file() -> str:
    return os.path.join(CFG.data_dir, ".installed_sha")


def _git(args: list[str], timeout: int = 30):
    import subprocess
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-c", f"safe.directory={BASE_DIR}", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _ensure_git_repo(token: str = ""):
    """Đảm bảo BASE_DIR đã được khởi tạo thành kho Git gắn với REPO_SLUG."""
    git_dir = os.path.join(BASE_DIR, ".git")
    remote_url = f"https://x-access-token:{token}@github.com/{REPO_SLUG}.git" if token else f"{REPO_URL}.git"
    if not os.path.isdir(git_dir):
        _git(["init", "-b", "main"])
        _git(["remote", "add", "origin", remote_url])
        _git(["fetch", "origin", "main"], timeout=45)
        _git(["reset", "--mixed", "origin/main"])
    else:
        _git(["remote", "set-url", "origin", remote_url])
    _git(["config", "user.name", REPO_OWNER])
    _git(["config", "user.email", f"{REPO_OWNER}@users.noreply.github.com"])


def _check_update_sync(force: bool = False) -> dict:
    """Kiểm tra kho GitHub ({REPO_SLUG}) xem có phiên bản mới hay commit mới không.
    Mặc định đệm 90 giây để tránh làm mới liên tục gây giới hạn tốc độ của GitHub API."""
    now = time.time()
    if not force and _UPDATE_CACHE["data"] and (now - _UPDATE_CACHE["ts"]) < 90:
        return _UPDATE_CACHE["data"]

    import requests
    token = _read_env_key("GITHUB_TOKEN")
    local_ver_obj = _read_local_version()
    local_version = str(local_ver_obj.get("version") or "1.5.0")

    has_git = os.path.isdir(os.path.join(BASE_DIR, ".git"))
    local_sha, local_msg, local_ts = "", "", 0
    if has_git:
        try:
            r_sha = _git(["rev-parse", "--short", "HEAD"])
            if r_sha.returncode == 0:
                local_sha = r_sha.stdout.strip()[:7]
            r_log = _git(["log", "-1", "--format=%s||%ct"])
            if r_log.returncode == 0 and "||" in r_log.stdout:
                parts = r_log.stdout.strip().split("||", 1)
                local_msg = parts[0]
                local_ts = int(parts[1])
        except Exception:
            pass

    if not local_sha and os.path.isfile(_installed_sha_file()):
        try:
            with open(_installed_sha_file(), encoding="utf-8") as f:
                local_sha = f.read().strip()[:7]
        except OSError:
            pass

    remote_version = local_version
    highlights = list(local_ver_obj.get("highlights") or [])
    try:
        rv = requests.get(
            f"https://raw.githubusercontent.com/{REPO_SLUG}/main/version.json?t={int(now)}",
            timeout=6,
        )
        if rv.status_code == 200:
            rvj = rv.json()
            if isinstance(rvj, dict):
                remote_version = str(rvj.get("version") or remote_version)
                if rvj.get("highlights"):
                    highlights = list(rvj["highlights"])
    except Exception:
        pass

    remote_sha, remote_msg, remote_time = "", "", ""
    recent_commits = []
    try:
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "muse2api-updater"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.get(
            f"https://api.github.com/repos/{REPO_SLUG}/commits?sha=main&per_page=5",
            headers=headers,
            timeout=6,
        )
        if resp.status_code == 200 and isinstance(resp.json(), list):
            commits = resp.json()
            for idx, c in enumerate(commits):
                sha7 = (c.get("sha") or "")[:7]
                c_msg = ((c.get("commit") or {}).get("message") or "").splitlines()[0]
                c_date = (((c.get("commit") or {}).get("committer") or {}).get("date") or "")
                c_url = c.get("html_url") or f"{REPO_URL}/commit/{sha7}"
                if idx == 0:
                    remote_sha = sha7
                    remote_msg = c_msg
                    remote_time = c_date
                recent_commits.append({
                    "sha": sha7,
                    "message": c_msg,
                    "date": c_date,
                    "url": c_url,
                })
    except Exception:
        pass

    # Nếu người dùng triển khai qua Docker/ZIP (không có .git) và lần chạy đầu khớp phiên bản, ghi lại SHA mốc ban đầu
    if not local_sha and remote_sha and local_version == remote_version:
        local_sha = remote_sha
        try:
            with open(_installed_sha_file(), "w", encoding="utf-8") as f:
                f.write(remote_sha)
        except OSError:
            pass

    def _parse_v(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in re.findall(r"\d+", v))
        except Exception:
            return (0,)

    has_update = False
    if remote_sha and local_sha:
        has_update = (remote_sha != local_sha)
    elif remote_version and local_version:
        has_update = _parse_v(remote_version) > _parse_v(local_version)

    data = {
        "repo_url": REPO_URL,
        "has_git": has_git,
        "local_version": local_version,
        "remote_version": remote_version,
        "local_sha": local_sha,
        "local_msg": local_msg,
        "local_ts": local_ts,
        "remote_sha": remote_sha,
        "remote_msg": remote_msg,
        "remote_time": remote_time,
        "has_update": has_update,
        "up_to_date": not has_update and bool(remote_sha or remote_version),
        "highlights": highlights,
        "recent_commits": recent_commits,
        "checked_at": int(now),
    }
    _UPDATE_CACHE["ts"] = now
    _UPDATE_CACHE["data"] = data
    return data


def _upgrade_from_github_sync() -> dict:
    """Kéo mã mới nhất từ GitHub để ghi đè các tệp cốt lõi (tương thích cả môi trường Git lẫn Docker/ZIP không có Git), tuyệt đối không đụng vào .env và data/."""
    import requests
    import tarfile

    token = _read_env_key("GITHUB_TOKEN")
    upgraded_via = ""
    try:
        _ensure_git_repo(token)
        f_res = _git(["fetch", "origin", "main"], timeout=45)
        if f_res.returncode == 0:
            _git(["checkout", "-f", "origin/main", "--", "."])
            _git(["reset", "--mixed", "origin/main"])
            upgraded_via = "git"
    except Exception as e:
        log.warning("Kéo cập nhật qua Git thất bại, sẽ dùng cách Tarball để cập nhật: %s", e)

    if not upgraded_via:
        resp = requests.get(
            f"https://codeload.github.com/{REPO_SLUG}/tar.gz/refs/heads/main",
            timeout=60,
        )
        if resp.status_code != 200:
            raise HTTPException(502, f"Tải gói cập nhật GitHub thất bại (HTTP {resp.status_code})")
        protected_files = {".env", "data/accounts.json", "data/tasks.json"}
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                rel = parts[1].replace("\\", "/")
                if ".." in rel or rel in protected_files:
                    continue
                target_path = os.path.join(BASE_DIR, rel)
                if member.isdir():
                    os.makedirs(target_path, exist_ok=True)
                elif member.isfile():
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    fobj = tar.extractfile(member)
                    if fobj is not None:
                        with open(target_path, "wb") as out_f:
                            out_f.write(fobj.read())
        upgraded_via = "tarball"

    _UPDATE_CACHE["ts"] = 0.0
    status = _check_update_sync(force=True)
    if status.get("remote_sha"):
        try:
            with open(_installed_sha_file(), "w", encoding="utf-8") as f:
                f.write(status["remote_sha"])
            status["local_sha"] = status["remote_sha"]
            status["has_update"] = False
            status["up_to_date"] = True
        except OSError:
            pass
    return {
        "ok": True,
        "via": upgraded_via,
        "message": f"Đã cập nhật thành công lên phiên bản mới nhất {status.get('remote_version')} ({status.get('remote_sha')})",
        "status": status,
    }


@app.get("/admin/update/check")
@app.get("/admin/repo/status")
async def admin_check_update(force: bool = False):
    """Cho mọi node đã triển khai kiểm tra thời gian thực xem kho chính thức trên GitHub có phiên bản mới không."""
    return await asyncio.to_thread(_check_update_sync, force)


@app.post("/admin/update/upgrade")
@app.post("/admin/repo/pull")
async def admin_upgrade_now(payload: dict = Body(default={}), _=Depends(auth)):
    """Một chạm kéo cập nhật mới nhất từ kho chính thức trên GitHub và tự động khởi động lại dịch vụ êm ái."""
    res = await asyncio.to_thread(_upgrade_from_github_sync)
    restart = payload.get("restart", True) if isinstance(payload, dict) else True
    if restart:
        def _delayed_restart():
            time.sleep(0.5)
            try:
                engine.stop()
            except Exception:
                pass
            os._exit(0)
        threading.Thread(target=_delayed_restart, daemon=True).start()
    return res


@app.post("/admin/repo/push")
async def admin_repo_push(payload: dict = Body(default={}), _=Depends(auth)):
    """Dành riêng cho người bảo trì: đẩy mã cốt lõi của node hiện tại lên kho GitHub (tự động lọc .env và thư mục data)."""
    msg = (payload.get("message") or "").strip() or f"chore: sync update ({time.strftime('%Y-%m-%d %H:%M:%S')})"
    new_token = (payload.get("github_token") or "").strip()
    if new_token:
        _persist_env("GITHUB_TOKEN", new_token)
        os.environ["GITHUB_TOKEN"] = new_token
    token = new_token or _read_env_key("GITHUB_TOKEN")

    def _do_push():
        _ensure_git_repo(token)
        existing_paths = [p for p in TRACKED_REPO_PATHS if os.path.exists(os.path.join(BASE_DIR, p))]
        _git(["add", "--", *existing_paths])
        st = _git(["status", "--porcelain", "--", *existing_paths])
        committed = False
        if st.stdout.strip():
            c_res = _git(["commit", "-m", msg])
            if c_res.returncode != 0:
                raise HTTPException(500, f"Git commit thất bại: {c_res.stderr or c_res.stdout}")
            committed = True
        p_res = _git(["push", "origin", "HEAD:main"], timeout=60)
        if p_res.returncode != 0:
            err = (p_res.stderr or p_res.stdout or "").strip()
            raise HTTPException(500, f"Git push thất bại: {err[:300]}")
        _UPDATE_CACHE["ts"] = 0.0
        status = _check_update_sync(force=True)
        return {
            "ok": True,
            "committed": committed,
            "message": "Đã commit và push thành công lên kho GitHub",
            "status": status,
        }

    return await asyncio.to_thread(_do_push)


@app.on_event("startup")
async def _startup():
    for task in list(store.tasks.values()):
        if task.get("kind") in ("image", "video") and task.get("status") in ("queued", "processing"):
            store.update_task(task["id"], status="failed", error="Dịch vụ khởi động lại đã ngắt tác vụ, vui lòng gửi lại")
    if not CFG.api_key:
        import secrets
        new_key = "m2a_" + secrets.token_hex(24)
        CFG.api_key = new_key
        _persist_env("MUSE2API_KEY", new_key)
        log.info("🔑 Không phát hiện MUSE2API_KEY, đã tự động tạo khóa ban đầu: %s", new_key)
    asyncio.create_task(_keepalive_loop())


@app.on_event("shutdown")
def _shutdown():
    engine.stop()
