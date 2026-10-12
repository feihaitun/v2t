"""yt-dlp 相关：读视频信息、挑字幕、下载音视频，以及把英文报错翻译成大白话。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import config
from .jobs import AttemptCtx, StrategyFailed
from .runner import run

# 字幕轨道里这些不是真字幕（B站的弹幕、直播聊天）
NOT_SUBTITLES = {"danmaku", "live_chat", "rechat", "comments"}

# YouTube 需要一个 JS 运行环境来解签名：有 deno 用 deno（yt-dlp 默认），没有就用 node
_NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
_DENO = shutil.which("deno")


def js_runtime_opts() -> dict:
    if _DENO or not _NODE:
        return {}
    return {"js_runtimes": {"node": {"path": _NODE}}}


def js_runtime_args() -> list[str]:
    if _DENO or not _NODE:
        return []
    return ["--js-runtimes", f"node:{_NODE}"]


def usable_formats(formats: list[dict]) -> list[dict]:
    """去掉 HLS 形式的字幕条目（m3u8），只留能直接下载的文件。"""
    return [f for f in formats or [] if f.get("protocol") != "m3u8_native" and (f.get("url") or f.get("data"))]

_ERROR_MAP = [
    (r"Fresh cookies", "抖音要求每个请求都带一个现场计算的签名，yt-dlp 目前算不出来，所以被拒了。"
                       "带访客 cookie 也没用（2026-10 实测）；不登录能下的办法是「专用浏览器打开抖音页面」。"),
    (r"Sign in to confirm|not a bot", "YouTube 怀疑是机器人，要求登录验证。"),
    (r"HTTP Error 429|Too Many Requests", "请求太频繁，被平台限流了（429）。过一会儿再试。"),
    (r"HTTP Error 403|Forbidden", "平台拒绝访问（403），通常是缺 cookie / Referer，或者链接过期了。"),
    (r"HTTP Error 404|Not Found", "页面或视频不存在（404），可能已删除或链接不完整。"),
    (r"Unsupported URL", "yt-dlp 不认识这种链接。"),
    (r"[Pp]rivate video|This video is private|仅自己可见", "视频是私密的，只有作者能看。"),
    (r"[Ll]ogin required|requires? (a )?login|need to log in|登录", "这个内容需要登录才能看。"),
    (r"Requested format is not available", "找不到符合要求的音视频格式。"),
    (r"timed out|Timeout|超时", "网络超时。"),
    (r"Unable to extract|Unable to download (webpage|JSON)", "页面结构变了或被拦，yt-dlp 没能从页面里读出视频信息。"),
    (r"This video is unavailable|Video unavailable", "视频不存在或已下架。"),
    (r"Only images are available", "平台只给了图片格式，没给音视频（通常是签名没解开）。"),
    (r"geo|not available in your country", "地区限制，这个地区看不了。"),
]


def explain_error(text: str) -> str:
    for pattern, plain in _ERROR_MAP:
        if re.search(pattern, text):
            return plain
    last = [ln for ln in text.splitlines() if "ERROR" in ln]
    return "yt-dlp 报错：" + (last[-1].replace("ERROR:", "").strip()[:200] if last else text.strip()[-200:])


class _Logger:
    """把 yt-dlp Python API 的日志转进任务日志（开发模式可见）。"""

    def __init__(self, attempt: AttemptCtx | None):
        self.attempt = attempt
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def debug(self, msg: str) -> None:
        if self.attempt and not msg.startswith("[debug] "):
            self.attempt.log(msg)

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)
        if self.attempt:
            self.attempt.log("yt-dlp 警告：" + msg, level="debug")

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        if self.attempt:
            self.attempt.log(msg, level="debug")


def extract_info(url: str, attempt: AttemptCtx | None = None, cookies: Path | None = None,
                 want_subs: bool = True) -> dict:
    """只读信息不下载。失败抛 StrategyFailed（带大白话原因）。"""
    import yt_dlp

    logger = _Logger(attempt)
    opts = {
        "quiet": True,
        "no_warnings": False,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "sleep_interval_requests": 0.75,
        "logger": logger,
        # 只读信息和字幕：哪怕视频格式解析失败（签名没解开等），字幕信息也照样要
        "ignore_no_formats_error": True,
        # 万一是合集/播放列表：只看第一条，绝不展开整个列表
        "playlist_items": "1",
        "extract_flat": "in_playlist",
        **js_runtime_opts(),
    }
    if want_subs:
        # 不设这两个，有的平台（比如 B站）根本不去读字幕信息
        opts.update(writesubtitles=True, writeautomaticsub=True)
    if cookies:
        opts["cookiefile"] = str(cookies)
    if attempt:
        attempt.log(f"yt-dlp 读取视频信息：{url}" + ("（带访客 cookie）" if cookies else ""))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            info = ydl.sanitize_info(info)
    except Exception as exc:  # yt_dlp.utils.DownloadError 等
        text = str(exc) + "\n" + "\n".join(logger.errors)
        raise StrategyFailed(explain_error(text), {"error": text[-1500:]}) from None
    if info.get("_type") == "playlist":
        raise StrategyFailed("这个链接是一个列表（频道 / 合集 / 播放列表），不是单个视频，请贴单个视频的链接", final=True)
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise StrategyFailed("这是正在进行（或还没开始）的直播，没法转写，等有回放后再试", final=True)
    has_subs = any(k not in NOT_SUBTITLES for k in (info.get("subtitles") or {})) or bool(info.get("automatic_captions"))
    has_formats = any(f.get("url") for f in info.get("formats") or [])
    if not has_subs and not has_formats and logger.warnings:
        # 格式和字幕都没拿到：把 yt-dlp 的警告翻译出来当原因（比如要登录、视频不存在）
        text = "\n".join(logger.warnings)
        raise StrategyFailed(explain_error(text), {"error": text[-1500:]})
    info["_v2t_warnings"] = list(logger.warnings)
    return info


def _is_translated(formats: list[dict]) -> bool:
    """YouTube 机器翻译的字幕轨道，下载地址里带 tlang= 参数。"""
    return any("tlang=" in (f.get("url") or "") for f in formats)


def pick_subtitle(info: dict, want_lang: str = "auto") -> dict | None:
    """挑字幕：原语言人工字幕 > 指定语言人工字幕 > 原语言自动字幕。机器翻译的轨道一律不要。

    原语言不清楚、又找不到确定的轨道时返回 None —— 宁可下载音频自己转写，也不拿错语言的字幕当原文。
    返回 {lang, kind: manual|auto, formats}。
    """
    manual = {k: usable_formats(v) for k, v in (info.get("subtitles") or {}).items() if k not in NOT_SUBTITLES}
    auto = {k: usable_formats(v) for k, v in (info.get("automatic_captions") or {}).items() if k not in NOT_SUBTITLES}
    manual = {k: v for k, v in manual.items() if v}
    # 去掉机器翻译：地址带 tlang=，或者键名是「目标语言-人工字幕语言」（比如 zh-Hans-en）
    auto = {k: v for k, v in auto.items()
            if v and not _is_translated(v) and not any(k.endswith("-" + m) and k != m for m in manual)}
    orig = (info.get("language") or "").lower() or None
    # YouTube 自动字幕里，原语言那一条带 -orig 后缀
    auto_orig = [k for k in auto if k.endswith("-orig")]
    if not orig and auto_orig:
        orig = auto_orig[0][: -len("-orig")]
    orig_known = bool(orig)

    def match(tracks: dict, lang: str | None) -> str | None:
        if not lang:
            return None
        lang = lang.lower()
        for key in tracks:
            if key.lower() == lang:
                return key
        for key in tracks:
            low = key.lower()
            if low.startswith(lang + "-") or low.replace("ai-", "") == lang:
                return key
        return None

    want = None if want_lang in ("", "auto") else want_lang
    candidates = [
        (orig, "manual", manual),
        (want, "manual", manual),
        (f"{orig}-orig" if orig else None, "auto", auto),
        (orig, "auto", auto),
    ]
    if not auto_orig:
        candidates.append((want, "auto", auto))
    for lang, kind, tracks in candidates:
        key = match(tracks, lang)
        if key:
            return {"lang": key, "kind": kind, "formats": tracks[key]}
    # 原语言不明、你也没指定语言：中文轨道优先（YouTube 没有自动字幕的视频多半是中文），只有一条就用那一条
    if manual and not orig_known and not want:
        zh = [k for k in manual if k.lower().startswith(("zh", "ai-zh", "yue"))]
        if zh:
            key = sorted(zh)[0]
        elif len(manual) == 1:
            key = next(iter(manual))
        else:
            return None
        return {"lang": key, "kind": "manual", "formats": manual[key]}
    return None


def download_subtitle(info: dict, choice: dict, out_dir: Path, attempt: AttemptCtx,
                      cookies: Path | None = None) -> Path:
    """用 yt-dlp 自己的网络会话下载字幕（带上它需要的请求头 / cookie）。"""
    import yt_dlp

    prefer = ["json3", "srv3", "vtt", "srt", "json", "ttml"]
    formats = [f for f in usable_formats(choice["formats"]) or choice["formats"] if f.get("ext") in prefer]
    if not formats:
        raise StrategyFailed("字幕格式不认识，没法读", {"exts": [f.get("ext") for f in choice["formats"]]})
    fmt = sorted(formats, key=lambda f: prefer.index(f["ext"]))[0]
    ext = fmt["ext"]
    safe_lang = re.sub(r"[^A-Za-z0-9_-]", "_", choice["lang"])[:40]
    path = out_dir / f"subtitle.{safe_lang}.{ext}"
    attempt.log(f"下载字幕轨道：{choice['lang']}（{'人工' if choice['kind'] == 'manual' else '自动'}字幕，格式 {ext}）")
    opts = {"quiet": True, "socket_timeout": 20, "logger": _Logger(attempt), **js_runtime_opts()}
    if cookies:
        opts["cookiefile"] = str(cookies)
    try:
        if fmt.get("data"):
            path.write_text(fmt["data"], encoding="utf-8")
        else:
            from yt_dlp.networking import Request

            with yt_dlp.YoutubeDL(opts) as ydl:
                extensions = {}
                if fmt.get("impersonate"):  # 像 yt-dlp 自己下载时一样，模拟浏览器指纹
                    try:
                        target, _ = ydl._parse_impersonate_targets(fmt["impersonate"])
                        if target:
                            extensions["impersonate"] = target
                    except Exception:
                        pass
                headers = fmt.get("http_headers") or info.get("http_headers") or {}
                data = ydl.urlopen(Request(fmt["url"], headers=headers, extensions=extensions)).read()
            path.write_bytes(data)
    except Exception as exc:
        raise StrategyFailed(explain_error(str(exc)), {"error": str(exc)[-800:], "track": choice["lang"]}) from None
    return path


def download_media(url: str, out_dir: Path, attempt: AttemptCtx, cookies: Path | None = None,
                   keep_video: bool = False, audio_format: str = "ba[ext=m4a]/ba/b",
                   extra_args: list[str] | None = None) -> Path:
    """下载音频（或视频）。默认只要音频：文件小、下得快。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = "bv*+ba/b" if keep_video else audio_format
    cmd = [
        # --ignore-config：不读你自己电脑上的 yt-dlp 配置（防止里面悄悄加了浏览器登录 cookie）
        config.PYTHON, "-I", "-m", "yt_dlp", "--ignore-config", "--no-cookies-from-browser",
        "--no-playlist", "--playlist-items", "1", "--match-filter", "!is_live",
        "--newline", "--no-mtime",
        "--socket-timeout", "20", "--retries", "3", "--sleep-requests", "0.75",
        "--ffmpeg-location", config.FFMPEG,
        "-f", fmt, "-o", str(out_dir / "media.%(ext)s"),
        "--write-info-json", "--no-write-playlist-metafiles",
        *js_runtime_args(), *(extra_args or []),
    ]
    if keep_video:
        cmd += ["--merge-output-format", "mp4"]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    cmd.append(url)
    result = run(cmd, job=attempt.job, step=attempt.step.key, strategy=attempt.strategy.key, timeout=1800,
                 cwd=out_dir)
    _shrink_info_json(out_dir)
    if "does not pass filter (!is_live)" in result.output:
        raise StrategyFailed("这是正在进行的直播，没法转写，等有回放后再试", final=True)
    media_exts = {".m4a", ".mp4", ".webm", ".mp3", ".aac", ".opus", ".ogg", ".flac", ".wav", ".mkv", ".mov", ".flv", ".m4s"}
    files = [p for p in out_dir.glob("media.*") if p.suffix.lower() in media_exts and p.is_file()]
    if not result.ok or not files:
        raise StrategyFailed(explain_error(result.output), {"yt_dlp_tail": result.tail(12), "returncode": result.returncode})
    return max(files, key=lambda p: p.stat().st_size)


_INFO_KEEP = ("id", "title", "fulltitle", "uploader", "channel", "creator", "author",
              "duration", "language", "webpage_url", "extractor_key")


def _shrink_info_json(out_dir: Path) -> None:
    """yt-dlp 写的 info.json 里有带你公网 IP 的签名下载地址；只留标题、时长这些需要的字段。"""
    import json

    path = out_dir / "media.info.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({k: data[k] for k in _INFO_KEEP if k in data}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except (OSError, ValueError):
        path.unlink(missing_ok=True)


def read_info_json(out_dir: Path) -> dict:
    import json

    path = out_dir / "media.info.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
