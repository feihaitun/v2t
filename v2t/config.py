"""全局路径和默认设置。"""

from __future__ import annotations

import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JOBS_DIR = DATA / "jobs"
UPLOADS_DIR = DATA / "uploads"
BROWSER_PROFILES_DIR = DATA / "browser-profile"
WEB_DIR = ROOT / "web"

PYTHON = sys.executable
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

DEFAULT_PORT = 8765


@dataclass
class Options:
    """一次任务的选项（前端表单 / 命令行参数都会转成它）。"""

    language: str = "auto"          # auto / zh / en / ja ...
    prefer_subtitles: bool = True    # 有现成字幕就直接用，不再转写
    douyin_cookie_subtitles: bool = False  # 独立可选模块：用 Chrome 的抖音 Cookie 查字幕
    douyin_cookie_profile: str = ""  # 空 = Chrome 最近使用的配置；不保存 Cookie 值
    douyin_chrome: bool = False  # 从正常登录的 Chrome 页面读取资料（独立连接器）
    allow_thirdparty: bool = False   # 允许把链接交给第三方解析网站（默认关）
    keep_video: bool = False         # 保留下载的视频文件（默认只留音频）
    official_only: bool = False      # 只取平台字幕和完整视频，不运行 Whisper
    initial_prompt: str = ""         # 给 Whisper 的提示词：人名、专业词
    headed_browser: bool = False     # 浏览器步骤是否弹出窗口（调试用）

    @classmethod
    def from_dict(cls, data: dict | None) -> "Options":
        data = data or {}
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        # API 只接受真正的布尔值授权；字符串 "false" 不能误开启账号访问。
        known["douyin_cookie_subtitles"] = data.get("douyin_cookie_subtitles") is True
        known["douyin_chrome"] = data.get("douyin_chrome") is True
        known["official_only"] = data.get("official_only") is True
        if known["official_only"]:
            known["prefer_subtitles"] = True
            known["keep_video"] = True
        profile = known.get("douyin_cookie_profile", "")
        known["douyin_cookie_profile"] = profile if isinstance(profile, str) else ""
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


def ensure_dirs() -> None:
    for path in (JOBS_DIR, UPLOADS_DIR, BROWSER_PROFILES_DIR):
        path.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILES_DIR.chmod(0o700)  # 专用浏览器的访客身份只有你自己能读
