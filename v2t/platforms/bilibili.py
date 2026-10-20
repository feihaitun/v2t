"""B站：实测（2026-10-11）结论 ——

- 下载音频不需要任何 cookie：yt-dlp 直接就能拿到全部音轨；
- CC 字幕 / AI 字幕必须登录（SESSDATA）才给，访客 cookie 没用。普通模式仍走访客下载与本机转写；
  用户明确开启原文件模式时，只读 Chrome 中 B站域名的登录 Cookie，保存平台原始字幕 JSON；
- 浏览器兜底：页面里的 window.__playinfo__ 有音频地址，下载时必须带 Referer: https://www.bilibili.com/（不带会 403）；
- b23.tv 短链接普通 HTTP 就能展开，但跳转地址里带着分享人的跟踪参数（buvid、mid），不记录、直接去掉。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from .. import config, ytdlp
from ..jobs import AttemptCtx, StrategyFailed
from .base import Ctx, Platform, Strategy, ensure_ok_media
from .common import BrowserGrab, _dl_dir


class BiliChromeSubtitles(Strategy):
    key = "bili_chrome_subtitles"
    label = "从 Chrome 登录状态取 B站原字幕"
    explain = "只读 Chrome 中 B站的登录 Cookie，向 B站查询字幕并保存平台返回的原始 JSON。"
    needs = "login"
    option_flag = "official_only"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        from ..douyin_cookie.cookies import CookieUnavailable, load_cookies
        from ..douyin_cookie.tracks import valid_segments

        try:
            cookies = load_cookies(domain="bilibili.com", login_names={"SESSDATA"})
        except CookieUnavailable:
            raise StrategyFailed("Chrome 中没有可读取的 B站登录状态，请先在 Chrome 登录 B站", expected=True) from None
        jar = httpx.Cookies()
        for cookie in cookies:
            jar.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
        headers = {"User-Agent": config.DESKTOP_UA, "Referer": ctx.url or "https://www.bilibili.com/"}
        try:
            with httpx.Client(cookies=jar, headers=headers, timeout=20, trust_env=False,
                              follow_redirects=False) as client:
                view = client.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": ctx.video_id})
                view.raise_for_status()
                payload = view.json()
                if payload.get("code") != 0:
                    raise StrategyFailed("B站没有交回视频页资料", expected=True)
                video = payload.get("data") or {}
                pages = video.get("pages") or []
                part = int(httpx.URL(ctx.url).params.get("p") or 1)
                if part < 1 or part > len(pages):
                    raise StrategyFailed("B站视频分 P 编号无效", final=True)
                cid = pages[part - 1].get("cid")
                reply = client.get("https://api.bilibili.com/x/player/wbi/v2",
                                   params={"bvid": ctx.video_id, "cid": cid})
                reply.raise_for_status()
                data = reply.json()
                if data.get("code") != 0:
                    raise StrategyFailed("B站没有交回字幕列表", expected=True)
                body = data.get("data") or {}
                tracks = ((body.get("subtitle") or {}).get("subtitles") or [])
                if not tracks:
                    reason = "B站要求登录才能查看字幕" if body.get("need_login_subtitle") else "这个视频没有返回平台字幕"
                    raise StrategyFailed(reason, expected=True)
                requested = ctx.options.language.split("-")[0]
                languages = ([requested] if requested not in ("auto", "zh", "en") else []) + (
                    ["en", "zh"] if requested == "en" else ["zh", "en"])
                found = []
                for language in languages:
                    choices = [t for t in tracks if
                               str(t.get("lan", "")).replace("ai-", "").split("-")[0] == language]
                    choices.sort(key=lambda t: str(t.get("lan", "")).startswith("ai-"))
                    for track in choices:
                        url = track.get("subtitle_url") or ""
                        if url.startswith("//"):
                            url = "https:" + url
                        parsed = urlparse(url)
                        host = parsed.hostname or ""
                        if (parsed.scheme != "https" or parsed.username or parsed.password or
                                not (host == "hdslb.com" or host.endswith(".hdslb.com") or
                                     host == "bilibili.com" or host.endswith(".bilibili.com"))):
                            continue
                        response = client.get(url)
                        if response.status_code != 200 or len(response.content) > 2_000_000:
                            continue
                        raw = response.json()
                        if not isinstance(raw, dict):
                            continue
                        lines = raw.get("body") or []
                        segments = valid_segments([{"start": x.get("from"), "end": x.get("to"),
                                                    "text": x.get("content")} for x in lines if isinstance(x, dict)])
                        if not segments:
                            continue
                        lang = str(track.get("lan") or "und")
                        safe_lang = re.sub(r"[^a-zA-Z0-9_-]", "_", lang)[:30]
                        path = ctx.workdir / f"subtitle.platform.{safe_lang}.json"
                        path.write_bytes(response.content)
                        found.append({"segments": segments, "language": language,
                                      "kind": "auto" if lang.startswith("ai-") else "manual",
                                      "how": f"B站平台字幕（{lang}）", "raw_file": path.name})
                        break
                if found:
                    ctx.remember_info({"title": video.get("title"), "duration": video.get("duration")})
                    ctx.subtitle_tracks = found
                    ctx.subtitle = found[0]
                    at.success("保存了 B站原始字幕：" + "、".join(
                        f"{t['language']} {len(t['segments'])} 段" for t in found))
                    return
        except StrategyFailed:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise StrategyFailed("B站字幕查询暂时失败，请检查 Chrome 登录状态", expected=True) from None
        raise StrategyFailed("字幕列表有记录，但没下载到可用的字幕文件", expected=True)

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
    label = "yt-dlp 下载 B站媒体（不带 cookie）"
    explain = "按任务选项下载完整视频或音频；媒体文件不需要登录 Cookie。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        out = _dl_dir(ctx, self.key)
        audio_format = "bv*+ba/b" if ctx.options.keep_video else "wa[vcodec=none]/ba"
        path = ytdlp.download_media(ctx.url, out, at, audio_format=audio_format,
                                    keep_video=ctx.options.keep_video)
        ctx.remember_info(ytdlp.read_info_json(out))
        ensure_ok_media(path, at)
        ctx.media_path, ctx.media_strategy = path, self.key
        kind = "完整视频" if ctx.options.keep_video else "音频"
        ctx.media_how = f"yt-dlp 直接下载{kind}（没用任何 cookie）"
        at.success(f"下载好了：{path.name}（{path.stat().st_size / 1e6:.1f}MB）")


class BiliChromeMedia(Strategy):
    key = "bili_chrome_media"
    label = "从 Chrome 登录状态下载 B站视频"
    explain = "只读 Chrome 中 B站的登录 Cookie，在本机内存里交给 yt-dlp 下载完整视频。"
    needs = "login"
    option_flag = "official_only"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        from ..douyin_cookie.cookies import CookieUnavailable, load_cookies

        try:
            cookies = load_cookies(domain="bilibili.com", login_names={"SESSDATA"})
        except CookieUnavailable:
            raise StrategyFailed("Chrome 中没有可读取的 B站登录状态；接下来试访客下载", expected=True) from None
        out = _dl_dir(ctx, self.key)
        path = ytdlp.download_media(ctx.url, out, at, keep_video=True, site_cookies=cookies)
        ctx.remember_info(ytdlp.read_info_json(out))
        ensure_ok_media(path, at)
        ctx.media_path, ctx.media_strategy = path, self.key
        ctx.media_how = "用 Chrome 中 B站的登录状态下载完整视频"
        at.success(f"下载好了完整视频：{path.stat().st_size / 1e6:.1f}MB")


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
