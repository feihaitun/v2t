"""其他网站：先试 yt-dlp（它认识上千个网站），不行就用真浏览器找 og:video / <video> 地址。"""

from __future__ import annotations

from .base import Platform, Strategy
from .common import BrowserGrab, YtdlpDirect, YtdlpSubtitles

GENERIC_PAGE_JS = r"""
() => {
  const out = [];
  const names = ["og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"];
  for (const n of names) {
    const el = document.querySelector(`meta[property="${n}"], meta[name="${n}"]`);
    if (el && el.content) out.push({ url: el.content, source: "og:video 标签" });
  }
  document.querySelectorAll("video, video source, audio, audio source").forEach((v) => {
    const s = v.currentSrc || v.src;
    if (s && !s.startsWith("blob:")) out.push({ url: s, source: "页面里的播放器" });
  });
  return out;
}
"""


class Generic(Platform):
    key = "generic"
    label = "其他网站"
    no_subtitle_reason = "不认识的网站，先试试 yt-dlp 能不能读到字幕。"

    def match(self, url: str) -> bool:
        return True

    def subtitle_strategies(self) -> list[Strategy]:
        return [YtdlpSubtitles()]

    def media_strategies(self) -> list[Strategy]:
        return [YtdlpDirect(), BrowserGrab(GENERIC_PAGE_JS)]
