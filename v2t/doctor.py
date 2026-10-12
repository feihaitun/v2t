"""环境自检：每一项都给出 正常 / 注意 / 有问题，以及怎么修。"""

from __future__ import annotations

import concurrent.futures as cf
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from . import config


def _check(key: str, label: str, status: str, detail: str, fix: str = "") -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, "fix": fix}


def check_ffmpeg() -> dict:
    if not Path(config.FFMPEG).exists():
        return _check("ffmpeg", "ffmpeg（抽音频）", "fail", "没找到 ffmpeg", "brew install ffmpeg")
    out = subprocess.run([config.FFMPEG, "-version"], capture_output=True, text=True).stdout.splitlines()
    return _check("ffmpeg", "ffmpeg（抽音频）", "ok", out[0] if out else config.FFMPEG)


def check_ytdlp(network: bool = True) -> dict:
    fix = "uv pip install --python .venv/bin/python -U \"yt-dlp[default,curl-cffi]\""
    try:
        from yt_dlp.version import __version__ as ver
    except ImportError:
        return _check("ytdlp", "yt-dlp（下载器）", "fail", "没装 yt-dlp", fix)
    try:
        y, m, d = (int(x) for x in ver.split(".")[:3])
        age = (date.today() - date(y, m, d)).days
    except ValueError:
        age = 0
    latest = None
    if network:
        try:
            import httpx

            latest = httpx.get("https://pypi.org/pypi/yt-dlp/json", timeout=6).json()["info"]["version"]
        except Exception:
            latest = None

    def norm(v: str) -> tuple:
        return tuple(int(x) for x in re.findall(r"\d+", v))

    if latest and norm(latest) > norm(ver):
        return _check("ytdlp", "yt-dlp（下载器）", "warn",
                      f"版本 {ver}，有新版本 {latest}。平台规则常变，旧版本容易突然下不了。", fix)
    if latest:
        return _check("ytdlp", "yt-dlp（下载器）", "ok", f"版本 {ver}，已是最新（{age} 天前发布）")
    if age > 90:
        return _check("ytdlp", "yt-dlp（下载器）", "warn", f"版本 {ver}，已经 {age} 天了，可能该更新了", fix)
    return _check("ytdlp", "yt-dlp（下载器）", "ok", f"版本 {ver}（{age} 天前发布；没联网，没法确认是否最新）")


def check_whisper() -> dict:
    try:
        import mlx_whisper  # noqa: F401
    except Exception as exc:
        return _check("whisper", "Whisper 语音识别", "fail", f"mlx-whisper 导入失败：{exc}",
                      "uv pip install --python .venv/bin/python mlx-whisper")
    name = "models--" + config.WHISPER_MODEL.replace("/", "--")
    model_dir = config.HF_CACHE / name
    if not model_dir.exists():
        return _check("whisper", "Whisper 语音识别", "warn",
                      f"模型 {config.WHISPER_MODEL} 还没下载，第一次转写时会自动下载（约 1.6GB）")
    size = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
    return _check("whisper", "Whisper 语音识别", "ok",
                  f"mlx-whisper 可用；模型 {config.WHISPER_MODEL.split('/')[-1]} 已在本机（{size / 1e9:.1f}GB）")


def check_browser(launch: bool = False) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _check("browser", "专用浏览器（Playwright）", "fail", "没装 playwright",
                      "uv pip install --python .venv/bin/python playwright")
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if not Path(exe).exists():
                return _check("browser", "专用浏览器（Playwright）", "fail", "Chromium 没下载",
                              ".venv/bin/python -m playwright install chromium")
            if launch:
                t0 = time.time()
                b = p.chromium.launch(headless=True)
                b.close()
                return _check("browser", "专用浏览器（Playwright）", "ok",
                              f"Chromium 能启动（{time.time() - t0:.1f}s）")
    except Exception as exc:
        return _check("browser", "专用浏览器（Playwright）", "fail", f"出错：{exc}",
                      ".venv/bin/python -m playwright install chromium")
    return _check("browser", "专用浏览器（Playwright）", "ok", "Chromium 已安装；只用来打开页面读视频地址（抖音 / 小红书 / B站兜底），只当访客，从不登录")


def check_js_runtime() -> dict:
    deno, node = shutil.which("deno"), shutil.which("node")
    if deno:
        return _check("jsruntime", "JS 运行环境（YouTube 用）", "ok", f"deno：{deno}")
    if node:
        return _check("jsruntime", "JS 运行环境（YouTube 用）", "ok",
                      f"没有 deno，用 node 代替：{node}（下载 YouTube 时会自动加 --js-runtimes node）",
                      "可选：brew install deno")
    return _check("jsruntime", "JS 运行环境（YouTube 用）", "warn",
                  "没有 deno 也没有 node，YouTube 视频可能只能拿字幕、下载不了", "brew install deno")


def check_disk() -> dict:
    free = shutil.disk_usage(config.ROOT).free
    status = "ok" if free > 5e9 else ("warn" if free > 1e9 else "fail")
    return _check("disk", "磁盘空间", status, f"剩余 {free / 1e9:.0f}GB")


def check_data_dir() -> dict:
    try:
        config.ensure_dirs()
        probe = config.DATA / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return _check("data", "数据目录", "fail", f"写不进去：{exc}")
    jobs = len(list(config.JOBS_DIR.glob("*/job.json")))
    return _check("data", "数据目录", "ok", f"{config.DATA}（已有 {jobs} 个任务记录）")


SITES = {
    "youtube": ("YouTube", "https://www.youtube.com/"),
    "bilibili": ("B站", "https://www.bilibili.com/"),
    "douyin": ("抖音", "https://www.douyin.com/"),
    "xiaohongshu": ("小红书", "https://www.xiaohongshu.com/"),
}


def check_network() -> list[dict]:
    import httpx

    def one(key: str) -> dict:
        label, url = SITES[key]
        t0 = time.time()
        try:
            r = httpx.get(url, timeout=8, follow_redirects=True, headers={"User-Agent": config.DESKTOP_UA})
            ms = int((time.time() - t0) * 1000)
            status = "ok" if r.status_code < 500 else "warn"
            return _check(f"net_{key}", f"能连上{label}", status, f"HTTP {r.status_code}，{ms}ms")
        except Exception as exc:
            return _check(f"net_{key}", f"能连上{label}", "fail", f"连不上：{type(exc).__name__}",
                          "检查网络 / 代理设置")

    with cf.ThreadPoolExecutor(4) as pool:
        return list(pool.map(one, SITES))


def _profile_cookie_names(profile: Path) -> list[str] | None:
    """读专用浏览器 cookie 数据库里的「名字」（不读内容）。复制一份再读，避免和正在运行的浏览器抢锁。"""
    import sqlite3
    import tempfile

    db = profile / "Default" / "Cookies"
    if not db.exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "Cookies"
        shutil.copy2(db, copy)
        con = sqlite3.connect(str(copy))
        try:
            return sorted({row[0] for row in con.execute("SELECT name FROM cookies")})
        finally:
            con.close()


def check_profiles() -> list[dict]:
    """每个平台的专用浏览器：有没有、里面有没有登录类 cookie（我们从不登录，出现了要提醒）。"""
    from .browser import LOGIN_COOKIE_NAMES

    out = []
    for key, (label, _url) in SITES.items():
        profile = config.BROWSER_PROFILES_DIR / key
        if not profile.exists():
            out.append(_check(f"profile_{key}", f"{label}专用浏览器", "ok", "还没用过（需要时自动创建，只当访客，不登录）"))
            continue
        try:
            names = _profile_cookie_names(profile)
        except Exception as exc:
            out.append(_check(f"profile_{key}", f"{label}专用浏览器", "warn", f"读不了 cookie 名单：{exc}"))
            continue
        login = sorted(set(names or []) & LOGIN_COOKIE_NAMES.get(key, set()))
        if login:
            out.append(_check(f"profile_{key}", f"{label}专用浏览器", "warn",
                              f"里面有登录类 cookie：{', '.join(login)}（有人在这个浏览器里登录过；我们不需要登录）",
                              f"删掉 data/browser-profile/{key} 文件夹，下次会自动重建一个干净的访客身份"))
        else:
            out.append(_check(f"profile_{key}", f"{label}专用浏览器", "ok",
                              f"只有访客身份，{len(names or [])} 个访客 cookie，没有任何登录信息"))
    return out


def run_all(network: bool = True, launch_browser: bool = False) -> dict:
    checks = [
        _check("python", "Python 环境", "ok", f"{sys.version.split()[0]}（{config.PYTHON}）"),
        check_ffmpeg(), check_ytdlp(network), check_whisper(), check_browser(launch_browser),
        check_js_runtime(), check_disk(), check_data_dir(),
    ]
    if network:
        checks += check_network()
    checks += check_profiles()
    worst = "ok"
    for c in checks:
        if c["status"] == "fail":
            worst = "fail"
        elif c["status"] == "warn" and worst == "ok":
            worst = "warn"
    return {"status": worst, "checks": checks, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
