"""B站：实测（2026-10-11）结论 ——

- 下载音频不需要任何 cookie：yt-dlp 直接就能拿到全部音轨；
- CC 字幕 / AI 字幕必须登录（SESSDATA）才给，访客 cookie 没用。我们不登录，所以 B 站一律走「下载音频 + 本机转写」，
  但会告诉你「这个视频其实有字幕，只是要登录」；
- 浏览器兜底：页面里的 window.__playinfo__ 有音频地址，下载时必须带 Referer: https://www.bilibili.com/（不带会 403）；
- b23.tv 短链接普通 HTTP 就能展开，但跳转地址里带着分享人的跟踪参数（buvid、mid），不记录、直接去掉。
"""

from __future__ import annotations

import re

import httpx

from .. import config, ytdlp
from ..jobs import AttemptCtx, StrategyFailed
from .base import Ctx, Platform, Strategy, ensure_ok_media
from .common import BrowserGrab, _dl_dir

BILI_PAGE_JS = r"""
() => {
  const out = [];
  const pi = window.__playinfo__;
  const st = window.__INITIAL_STATE__ || {};
  const vd = st.videoData || {};
  const meta = { title: vd.title, duration: vd.duration, author: (vd.owner || {}).name };
  const audio = ((pi && pi.data && pi.data.dash && pi.data.dash.audio) || []).slice()
    .sort((a, b) => (a.bandwidth || 0) - (b.bandwidth || 0));
  for (const a of audio) {
    for (const u of [a.baseUrl || a.base_url, ...((a.backupUrl || a.backup_url) || [])]) {
      if (u) out.push({ url: u, kind: "audio", source: "页面数据 __playinfo__（音频）", ...meta });
    }
  }
  return out;
}
"""


class BiliSubtitleCheck(Strategy):
    key = "bili_subtitle_check"
    label = "看看 B站有没有字幕"
    explain = ("用 yt-dlp 读视频信息，顺便看有没有 CC / AI 字幕。B站的字幕要登录才给，我们不登录，"
               "所以有字幕也只是告诉你一声，然后直接转写。")
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        info = ytdlp.extract_info(ctx.url, at, want_subs=True)
        ctx.remember_info(info)
        subs = {k: v for k, v in (info.get("subtitles") or {}).items() if k not in ytdlp.NOT_SUBTITLES and v}
        if subs:  # 只有带登录 cookie 才会走到这里
            choice = ytdlp.pick_subtitle(info, ctx.options.language)
            if choice:
                from ..subtitles import parse_file

                path = ytdlp.download_subtitle(info, choice, ctx.workdir, at)
                segments = parse_file(path)
                if len(segments) >= 2:
                    ctx.subtitle = {"segments": segments, "language": choice["lang"].replace("ai-", ""),
                                    "kind": choice["kind"], "track": choice["lang"], "file": path.name,
                                    "how": f"B站字幕（轨道 {choice['lang']}）"}
                    at.success(f"拿到字幕：{choice['lang']}，{len(segments)} 段")
                    return
        warned = any("only available when logged in" in w for w in info.get("_v2t_warnings", []))
        if warned:
            raise StrategyFailed("B站的字幕要登录才给（我们不登录，不冒账号风险），直接下载音频自己转写", expected=True)
        raise StrategyFailed("这个视频没有字幕", expected=True)


class BiliYtdlpAudio(Strategy):
    key = "bili_ytdlp_audio"
    label = "yt-dlp 下载音频（不带 cookie）"
    explain = "B站下载音频不需要任何 cookie。只要最小的那条音轨（约 64kbps），对语音识别足够了。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        out = _dl_dir(ctx, self.key)
        audio_format = "bv*+ba/b" if ctx.options.keep_video else "wa[vcodec=none]/ba"
        path = ytdlp.download_media(ctx.url, out, at, audio_format=audio_format)
        ctx.remember_info(ytdlp.read_info_json(out))
        ensure_ok_media(path, at)
        ctx.media_path, ctx.media_strategy = path, self.key
        ctx.media_how = "yt-dlp 直接下载音频（没用任何 cookie）"
        at.success(f"下载好了：{path.name}（{path.stat().st_size / 1e6:.1f}MB）")


class Bilibili(Platform):
    key = "bilibili"
    label = "B站"
    hosts = ("bilibili.com",)
    short_hosts = ("b23.tv", "bili2233.cn")
    id_patterns = (r"/video/(BV[0-9A-Za-z]{10})", r"/video/(av\d+)", r"\b(BV[0-9A-Za-z]{10})\b")
    collection_patterns = (r"//space\.bilibili\.com", r"//live\.bilibili\.com", r"bilibili\.com/(list|medialist)/", r"favlist")

    def expand_short(self, url: str, log) -> str | None:
        loc = None
        with httpx.Client(headers={"User-Agent": config.DESKTOP_UA}, timeout=15) as client:
            current = url
            for _hop in range(5):  # http://b23.tv 会先跳到 https://b23.tv，再跳到视频页
                r = client.get(current, follow_redirects=False)
                nxt = r.headers.get("location")
                if r.status_code not in (301, 302, 303, 307, 308) or not nxt:
                    break
                loc = str(httpx.URL(current).join(nxt))
                if (httpx.URL(loc).host or "") not in self.short_hosts:
                    break
                current = loc
        if not loc:
            return None
        # 跳转地址里有分享人的 buvid / mid 等跟踪参数：只保留视频编号和分 P
        u = httpx.URL(loc)
        m = re.search(r"/video/(BV[0-9A-Za-z]{10}|av\d+)", loc)
        bvid = u.params.get("bvid") or ""  # 活动页之类的跳转地址，视频编号在 ?bvid= 里
        vid = m.group(1) if m else (bvid if re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid) else None)
        p = u.params.get("p")
        clean = f"https://www.bilibili.com/video/{vid}" if vid else loc.split("?")[0]
        if p and p != "1":
            clean += f"?p={p}"
        log(f"短链接展开（已去掉分享人的跟踪参数）：{clean}")
        return clean

    def canonical_url(self, url: str, video_id: str | None) -> str:
        if not video_id:
            return url
        p = httpx.URL(url).params.get("p")
        return f"https://www.bilibili.com/video/{video_id}" + (f"?p={p}" if p and p != "1" else "")

    def subtitle_strategies(self) -> list[Strategy]:
        return [BiliSubtitleCheck()]

    def media_strategies(self) -> list[Strategy]:
        return [
            BiliYtdlpAudio(),
            BrowserGrab(BILI_PAGE_JS, url_patterns=(".m4s",), referer="https://www.bilibili.com/",
                        wait_ms=5000, prefer_audio=True),
        ]
