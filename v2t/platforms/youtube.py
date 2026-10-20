"""YouTube：普通模式使用公开字幕；原文件模式优先使用 Chrome 登录状态取中英文字幕和视频。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from ..douyin_cookie.cookies import CookieUnavailable, load_cookies
from .. import ytdlp
from ..jobs import AttemptCtx, StrategyFailed
from ..subtitles import parse_file
from .base import Ctx, Platform, Strategy, ensure_ok_media
from .common import YtdlpDirect, YtdlpSubtitles, _dl_dir


def _youtube_cookies() -> list[dict]:
    return load_cookies(domain="youtube.com",
                        login_names={"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"})


def _is_original_auto_format(fmt: dict) -> bool:
    return "tlang" not in parse_qs(urlparse(fmt.get("url") or "").query)


def _is_translation_from(fmt: dict, original: str) -> bool:
    query = parse_qs(urlparse(fmt.get("url") or "").query)
    return bool(query.get("tlang")) and query.get("lang", [""])[0].split("-")[0] == original


def _caption_choices(info: dict, language: str) -> list[dict]:
    """人工原字幕 → 平台自动原字幕 → 平台机翻；不把机翻冒充原字幕。"""
    choices = []
    # 平台的「-orig」轨道比视频语言元数据更可信；两者在真实视频中会冲突。
    original = next((key.split("-")[0].lower() for key in (info.get("automatic_captions") or {})
                     if key.endswith("-orig")), "")
    if not original:
        original = str(info.get("language") or "").split("-")[0].lower()
    for kind, tracks in (("manual", info.get("subtitles") or {}),
                         ("auto", info.get("automatic_captions") or {})):
        for key, formats in tracks.items():
            if key in ytdlp.NOT_SUBTITLES or key.lower().split("-")[0] != language:
                continue
            usable = ytdlp.usable_formats(formats)
            if not usable:
                continue
            if kind == "auto":
                if language == original:
                    usable = [f for f in usable if _is_original_auto_format(f)]
                    real_kind = "auto"
                elif original:
                    usable = [f for f in usable if _is_translation_from(f, original)]
                    real_kind = "translated"
                else:
                    continue
                if not usable:
                    continue
            else:
                real_kind = "manual"
            choices.append({"lang": key, "kind": real_kind, "formats": usable})
    choices.sort(key=lambda x: (0 if x["kind"] == "manual" else 1 if x["kind"] == "auto" else 2,
                                0 if x["lang"].lower() == language else 1,
                                0 if x["lang"].endswith("-orig") else 1,
                                len(x["lang"])))
    return choices


def _download_tracks(ctx: Ctx, at: AttemptCtx, cookies: list[dict] | None) -> None:
    info = ytdlp.extract_info(ctx.url, at, site_cookies=cookies)
    ctx.remember_info(info)
    requested = ctx.options.language.split("-")[0]
    languages = ([requested] if requested not in ("auto", "zh", "en") else []) + (
        ["en", "zh"] if requested == "en" else ["zh", "en"])
    found = []
    for language in languages:
        for choice in _caption_choices(info, language):
            try:
                path = ytdlp.download_subtitle(info, choice, ctx.workdir, at, site_cookies=cookies)
                segments = parse_file(path)
            except (StrategyFailed, ValueError, OSError):
                continue
            if len(segments) < 2:
                continue
            kind_label = {"manual": "人工", "auto": "平台自动", "translated": "平台机翻"}[choice["kind"]]
            found.append({"segments": segments, "language": language, "kind": choice["kind"],
                          "how": f"YouTube {kind_label}字幕（{choice['lang']}）",
                          "raw_file": path.name})
            break
    if not found:
        raise StrategyFailed("YouTube 没有交回可用的中英文字幕", expected=True)
    ctx.subtitle_tracks = found
    ctx.subtitle = found[0]
    at.success("保存了 YouTube 平台字幕：" + "、".join(
        f"{t['language']} {len(t['segments'])} 段" for t in found))


class YouTubeChromeSubtitles(Strategy):
    key = "youtube_chrome_subtitles"
    label = "从 Chrome 登录状态取 YouTube 中英文字幕"
    explain = "只读 Chrome 中 YouTube 的登录 Cookie，优先保存平台人工字幕，其次自动字幕；中英文分别保存。"
    needs = "login"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        try:
            cookies = _youtube_cookies()
        except CookieUnavailable:
            raise StrategyFailed("Chrome 中没有可读取的 YouTube 登录状态；接下来试公开字幕", expected=True) from None
        _download_tracks(ctx, at, cookies)


class YouTubeGuestSubtitles(Strategy):
    key = "youtube_guest_subtitles"
    label = "读取 YouTube 公开的中英文字幕"
    explain = "登录状态无法使用时，读取公开视频的人工或自动字幕，中英文分别保存。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        _download_tracks(ctx, at, None)


class YouTubeChromeMedia(Strategy):
    key = "youtube_chrome_media"
    label = "从 Chrome 登录状态下载 YouTube 视频"
    explain = "只读 Chrome 中 YouTube 的登录 Cookie，在本机内存里交给 yt-dlp 下载完整视频。"
    needs = "login"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        try:
            cookies = _youtube_cookies()
        except CookieUnavailable:
            raise StrategyFailed("Chrome 中没有可读取的 YouTube 登录状态；接下来试公开下载", expected=True) from None
        out = _dl_dir(ctx, self.key)
        path = ytdlp.download_media(ctx.url, out, at, keep_video=True, site_cookies=cookies)
        ctx.remember_info(ytdlp.read_info_json(out))
        ensure_ok_media(path, at)
        ctx.media_path, ctx.media_strategy = path, self.key
        ctx.media_how = "用 Chrome 中 YouTube 的登录状态下载完整视频"
        at.success(f"下载好了完整视频：{path.stat().st_size / 1e6:.1f}MB")


class YtdlpEmbeddedClient(Strategy):
    key = "ytdlp_web_embedded"
    label = "yt-dlp 换「嵌入播放器」通道"
    explain = "默认通道被拦时，假装成网页里嵌入的播放器再下载一次（需要本机的 JS 运行环境解签名）。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        out = _dl_dir(ctx, self.key)
        path = ytdlp.download_media(ctx.url, out, at, keep_video=ctx.options.keep_video,
                                    extra_args=["--extractor-args", "youtube:player_client=web_embedded"])
        ctx.remember_info(ytdlp.read_info_json(out))
        ensure_ok_media(path, at)
        ctx.media_path, ctx.media_strategy = path, self.key
        ctx.media_how = "yt-dlp（嵌入播放器通道）直接下载，没用 cookie"
        at.success(f"下载好了：{path.name}")


class YtdlpRetryLater(Strategy):
    key = "ytdlp_retry_later"
    label = "等一会儿再用默认通道试一次"
    explain = "YouTube 偶尔会临时拒绝下载（403），过十几秒通常就好了。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        import time

        at.log("等 15 秒再试…", level="info")
        time.sleep(15)
        out = _dl_dir(ctx, self.key)
        path = ytdlp.download_media(ctx.url, out, at, keep_video=ctx.options.keep_video)
        ctx.remember_info(ytdlp.read_info_json(out))
        ensure_ok_media(path, at)
        ctx.media_path, ctx.media_strategy = path, self.key
        ctx.media_how = "yt-dlp 直接下载（等了一会儿再试才成功，没用 cookie）"
        at.success(f"下载好了：{path.name}")


class YouTube(Platform):
    key = "youtube"
    label = "YouTube"
    hosts = ("youtube.com", "youtube-nocookie.com")
    short_hosts = ("youtu.be",)
    id_patterns = (
        r"[?&]v=([\w-]{11})", r"youtu\.be/([\w-]{11})", r"/shorts/([\w-]{11})",
        r"/live/([\w-]{11})", r"/embed/([\w-]{11})",
    )
    collection_patterns = (r"youtube\.com/@", r"youtube\.com/(channel|c|user)/", r"youtube\.com/playlist", r"/(streams|featured|videos)/?$")

    def is_short(self, url: str) -> bool:
        return False  # youtu.be 链接自己就带视频编号，不用展开

    def canonical_url(self, url: str, video_id: str | None) -> str:
        # 统一成 watch?v=，顺便去掉 &list= 防止下载整个播放列表
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else url

    def subtitle_strategies(self) -> list[Strategy]:
        return [YtdlpSubtitles()]

    def media_strategies(self) -> list[Strategy]:
        return [YtdlpDirect(), YtdlpEmbeddedClient(), YtdlpRetryLater()]
