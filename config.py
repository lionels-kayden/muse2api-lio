"""Cấu hình muse2api."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

_DEFAULT_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """Tự động nạp file .env ở thư mục gốc dự án (không ghi đè biến môi trường hệ thống đã có)."""
    base = os.environ.get("MUSE2API_HOME") or _DEFAULT_BASE_DIR
    env_path = os.path.join(base, ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_dotenv()


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _detect_chromium() -> str:
    """Ưu tiên dùng MUSE2API_CHROMIUM; nếu chưa đặt hoặc đường dẫn không tồn tại thì tự dò Chromium/Chrome đã cài trên hệ thống."""
    configured = os.environ.get("MUSE2API_CHROMIUM", "").strip()
    if configured and (os.path.isfile(configured) or shutil.which(configured)):
        return configured
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if os.path.isfile(c) or shutil.which(c):
            return c
    return configured or "chromium"


@dataclass
class Config:
    base_dir: str = field(default_factory=lambda: _env("MUSE2API_HOME", _DEFAULT_BASE_DIR))
    host: str = field(default_factory=lambda: _env("MUSE2API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("MUSE2API_PORT", "18610")))

    # Trình duyệt
    chromium: str = field(default_factory=_detect_chromium)
    cdp_port: int = field(default_factory=lambda: int(_env("MUSE2API_CDP_PORT", "19210")))
    home_dir: str = field(default_factory=lambda: _env("MUSE2API_HOME_DIR", os.path.expanduser("~")))
    extra_path: str = field(default_factory=lambda: _env("MUSE2API_EXTRA_PATH", "/snap/bin"))
    site_url: str = field(default_factory=lambda: _env("MUSE2API_SITE", "https://muse.ai/"))
    login_wait: int = field(default_factory=lambda: int(_env("MUSE2API_LOGIN_WAIT", "60")))

    # Thư mục gốc chứa profile trình duyệt.
    # Vấp rồi mới biết: chromium bản snap bị AppArmor giới hạn, chỉ đọc/ghi được đường dẫn dưới $HOME.
    # Truyền --user-data-dir=/opt/... hay /tmp/... sẽ không báo lỗi, nhưng bị bỏ qua âm thầm,
    # trình duyệt rơi về ~/snap/chromium/common/chromium.
    # Nên bắt buộc phải nằm dưới $HOME (đừng dùng thư mục ẩn bắt đầu bằng dấu chấm, giao diện home của snap loại trừ file ẩn).
    profile_root: str = field(
        default_factory=lambda: _env("MUSE2API_PROFILE_ROOT",
                                     os.path.join(os.path.expanduser("~"),
                                                  "muse2api-profiles")))

    # Xác thực
    api_key: str = field(default_factory=lambda: _env("MUSE2API_KEY", ""))

    # Địa chỉ công khai (dùng để trang quản trị hiển thị "địa chỉ kết nối"; để trống thì trang dùng tên miền hiện tại thay thế)
    public_base: str = field(
        default_factory=lambda: _env("MUSE2API_PUBLIC_BASE", ""))

    # Các nguồn được phép gọi liên miền tới API nhập (dùng khi script trợ giúp Cookie gửi từ trang muse.ai)
    cors_origins: str = field(
        default_factory=lambda: _env("MUSE2API_CORS_ORIGINS",
                                     "https://muse.ai,https://www.muse.ai"))

    # Sinh tạo
    image_timeout: int = field(default_factory=lambda: int(_env("MUSE2API_IMAGE_TIMEOUT", "240")))
    video_timeout: int = field(default_factory=lambda: int(_env("MUSE2API_VIDEO_TIMEOUT", "600")))
    chat_timeout: int = field(default_factory=lambda: int(_env("MUSE2API_CHAT_TIMEOUT", "300")))
    max_concurrency: int = field(default_factory=lambda: max(1, int(_env("MUSE2API_CONCURRENCY", "2"))))

    # Công tắc thích ứng giao thức gọi công cụ (function calling), **mặc định tắt**.
    # Thực đo cho thấy trợ lý của muse.ai từ chối thẳng việc xuất "gọi công cụ giả"
    # (nguyên văn: "Tôi sẽ không xuất 'gọi công cụ giả' theo định dạng JSON này"),
    # nhồi ép giao thức vào còn làm hỏng câu trả lời bình thường.
    # Nên mặc định chỉ làm "tương thích tham số" (client truyền tools sẽ không bị 422), không nhồi, không phân tích.
    # Nếu hành vi model mục tiêu thay đổi, có thể đặt MUSE2API_TOOL_PROTOCOL=1 để bật lớp thích ứng này rồi thử lại.
    tool_protocol: bool = field(
        default_factory=lambda: _env("MUSE2API_TOOL_PROTOCOL", "0").strip() != "0")

    # Cấu hình giữ sống: có giữ sống tự động cho tài khoản đã bị vô hiệu hay không, **mặc định tắt**.
    # Khi bật, tài khoản bị quản trị viên vô hiệu thủ công trong nền vẫn được tiến trình giữ sống nền
    # tự động giữ sống và đánh thức VM, duy trì phiên không hết hạn, nhưng vẫn không tham gia
    # điều phối sinh ảnh, sinh video và luân chuyển hội thoại bình thường.
    keepalive_disabled_accounts: bool = field(
        default_factory=lambda: _env("MUSE2API_KEEPALIVE_DISABLED_ACCOUNTS", "0").strip() != "0")

    @property
    def data_dir(self) -> str:
        return os.path.join(self.base_dir, "data")

    @property
    def media_dir(self) -> str:
        return os.path.join(self.data_dir, "media")

    @property
    def download_dir(self) -> str:
        return os.path.join(self.data_dir, "downloads")

    @property
    def profile_dir(self) -> str:
        """Profile trình duyệt dùng cho sinh tạo."""
        return os.path.join(self.profile_root, "generate")

    @property
    def accounts_file(self) -> str:
        return os.path.join(self.data_dir, "accounts.json")

    @property
    def tasks_file(self) -> str:
        return os.path.join(self.data_dir, "tasks.json")

    def ensure_dirs(self):
        for d in (self.base_dir, self.data_dir, self.media_dir, self.download_dir,
                  self.profile_dir):
            os.makedirs(d, exist_ok=True)


CFG = Config()
