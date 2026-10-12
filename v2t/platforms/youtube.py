"""YouTube：大多数英文视频有字幕（人工或自动），中文视频经常没有 → 下载音频转写。

实测（2026-10-11）：不需要 cookie、不需要浏览器、不需要登录。
"""

from __future__ import annotations

from .. import ytdlp
from ..jobs import AttemptCtx
from .base import Ctx, Platform, Strategy, ensure_ok_media
from .common import YtdlpDirect, YtdlpSubtitles, _dl_dir


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
