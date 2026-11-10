"""X / Twitter：公开帖子优先取平台字幕，再下载完整视频。"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import Platform, Strategy
from .common import BrowserGrab, YtdlpDirect, YtdlpSubtitles
from .generic import GENERIC_PAGE_JS


class Twitter(Platform):
    key = "twitter"
    label = "X / Twitter"
    hosts = ("x.com", "twitter.com")
    no_subtitle_reason = "X 没有交回可用的字幕时，可以下载视频后在本机转写。"

    def subtitle_strategies(self) -> list[Strategy]:
        return [YtdlpSubtitles()]

    def media_strategies(self) -> list[Strategy]:
        return [YtdlpDirect(), BrowserGrab(GENERIC_PAGE_JS)]

    def video_id(self, url: str) -> str | None:
        match = re.search(r"/status/(\d+)(?:/|$)", urlparse(url).path)
        return match.group(1) if match else None

    def canonical_url(self, url: str, video_id: str | None) -> str:
        if not video_id:
            return url
        parsed = urlparse(url)
        # 一个帖子可有多个视频；保留 /video/N，yt-dlp 才会选中用户指定的那条。
        video = re.search(r"/video/(\d+)(?:/|$)", parsed.path)
        suffix = f"/video/{video.group(1)}" if video else ""
        user = parsed.path.split("/status/", 1)[0].strip("/")
        return f"https://{parsed.hostname}/{user}/status/{video_id}{suffix}"

    def collection_reason(self, url: str) -> str | None:
        if not self.video_id(url):
            return "这是 X 主页或其他页面链接，请复制具体帖子的 /status/ 视频链接。"
        return None
