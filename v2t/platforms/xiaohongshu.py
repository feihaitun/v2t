"""小红书：实测（2026-10-11）结论 ——

- 电脑版网页对没登录的人一律跳转登录页，以前「读 og:video 标签」的办法已经失效；
- 用「手机版网页」（iPhone 的 User-Agent）打开就不用登录，页面数据 window.__INITIAL_STATE__ 里有：
  视频文件地址（多种清晰度）+ 平台自动生成的字幕（mediaV2.video.subtitles，srt 格式）；
- 链接里必须带 xsec_token（从 App 复制的分享链接都带），短链接 xhslink.com 普通 HTTP 就能展开；
- 视频 CDN 不需要 cookie / Referer；
- yt-dlp 现在对小红书无效（No video formats found!），只作为最后的备用。
"""

from __future__ import annotations

import json
import re

import httpx

from ..browser import download_url
from ..jobs import AttemptCtx, StrategyFailed
from ..subtitles import parse_vtt_or_srt
from .base import Ctx, Platform, Strategy, ensure_ok_media
from .common import _dl_dir

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1")
_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", re.S)
DEAD_LINK = "链接失效或笔记看不了（可能已删除、设为私密，或者分享链接太旧）。请从小红书 App 重新复制分享链接。"


def _check_blocked(final_url: str) -> None:
    if "/website-login/error" in final_url:
        raise StrategyFailed("小红书提示「安全限制：IP 存在风险」（300012），换个网络环境或过一会儿再试")
    if "/login" in final_url:
        raise StrategyFailed("被跳到登录页了（通常是链接缺少 xsec_token，或用了电脑版网页）")
    if "/404" in final_url:
        code = httpx.URL(final_url).params.get("error_code")
        raise StrategyFailed(DEAD_LINK, {"error_code": code}, final=True)


def _note_from_state(state: dict | None) -> dict | None:
    nd = (((state or {}).get("noteData") or {}).get("data") or {}).get("noteData")
    if nd and nd.get("type"):
        return nd
    for v in (((state or {}).get("note") or {}).get("noteDetailMap") or {}).values():
        if (v.get("note") or {}).get("type"):
            return v["note"]
    return None


def fetch_note(ctx: Ctx, at: AttemptCtx) -> dict:
    """用手机版网页（普通 HTTP，不带 cookie）读笔记数据；同一个任务里只读一次。"""
    if ctx.info.get("xhs_note"):
        return ctx.info["xhs_note"]
    at.log("用手机版网页（iPhone User-Agent，不带 cookie）读取笔记数据", level="info")
    with httpx.Client(headers={"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                      timeout=30, follow_redirects=True) as client:
        r = client.get(ctx.url)
    at.log(f"页面返回 {r.status_code}，最终地址：{str(r.url)[:160]}")
    _check_blocked(str(r.url))
    m = _STATE_RE.search(r.text)
    if not m:
        raise StrategyFailed("手机版页面里没有找到笔记数据（可能是验证页，或者页面结构变了）", {"http_status": r.status_code})
    state = json.loads(re.sub(r"\bundefined\b", "null", m.group(1)))
    note = _note_from_state(state)
    if not note:
        raise StrategyFailed(DEAD_LINK, {"reason": "noteData 为空"})
    _remember(ctx, note)
    return note


def _remember(ctx: Ctx, note: dict) -> None:
    ctx.info["xhs_note"] = note
    streams = _streams(note)
    duration = (streams[0].get("duration") or 0) / 1000 if streams else None
    ctx.remember_info({"title": note.get("title") or (note.get("desc") or "")[:40],
                       "uploader": (note.get("user") or {}).get("nickname") or (note.get("user") or {}).get("nickName"),
                       "duration": duration})


def _streams(note: dict) -> list[dict]:
    stream = (((note.get("video") or {}).get("media") or {}).get("stream") or {})
    items = [s for lst in stream.values() for s in (lst or []) if s.get("masterUrl")]
    return sorted(items, key=lambda s: s.get("size") or 1 << 62)  # 最小的那档就够（我们只要声音）


def _media_v2(note: dict) -> dict:
    raw = (note.get("video") or {}).get("mediaV2")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return (raw or {}).get("video") or {}


class XhsSubtitles(Strategy):
    key = "xhs_subtitles"
    label = "读小红书自动字幕"
    explain = "小红书会给很多有人声的视频自动生成字幕。用手机版网页读出来（不用登录、不带 cookie）。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        note = fetch_note(ctx, at)
        if note.get("type") != "video":
            raise StrategyFailed("这条笔记不是视频（是图文笔记），没有声音可以转写", final=True)
        v2 = _media_v2(note)
        flags = v2.get("opaque1") or {}
        tracks = v2.get("subtitles") or {}
        at.log(f"字幕轨道：{list(tracks) or '无'}；有人声：{flags.get('hasHumanVoice')}；语言：{flags.get('videoLanguage')}",
               level="info")
        if not tracks:
            raise StrategyFailed("这条视频没有平台字幕（常见于纯音乐或者没人说话的视频）", expected=True)
        order = ["source", "zh-CN"] + [k for k in tracks if k not in ("source", "zh-CN", "en-US")] + ["en-US"]
        with httpx.Client(timeout=20) as client:
            for lang in order:
                for track in tracks.get(lang) or []:
                    if not str(track.get("url", "")).startswith("http"):
                        continue
                    r = client.get(track["url"])
                    if r.status_code == 200 and b"-->" in r.content:
                        path = ctx.workdir / f"subtitle.xhs-{lang}.srt"
                        path.write_bytes(r.content)
                        segments = parse_vtt_or_srt(r.content.decode("utf-8", errors="replace"), dedupe=False)
                        if len(segments) < 2:
                            continue
                        langs = flags.get("videoLanguage") or []
                        language = (langs[0] if isinstance(langs, list) and langs else
                                    "zh" if lang in ("source", "zh-CN") else lang).split("-")[0]
                        ctx.subtitle = {
                            "segments": segments, "language": language, "kind": "auto", "track": lang,
                            "file": path.name, "how": f"小红书平台自动生成的字幕（轨道 {lang}）",
                        }
                        at.success(f"拿到小红书字幕：{lang}，{len(segments)} 段")
                        return
        raise StrategyFailed("字幕地址都下载失败了")


class XhsMobileHttp(Strategy):
    key = "xhs_mobile_http"
    label = "手机版网页拿视频地址"
    explain = "用 iPhone 的身份打开手机版网页（不登录、不带 cookie），从页面数据里拿到视频文件地址，直接下载最小的那档。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        note = fetch_note(ctx, at)
        _download_from_note(ctx, at, note, self.key, "手机版网页（普通 HTTP，没有登录也没有 cookie）")


class XhsMobileBrowser(Strategy):
    key = "xhs_mobile_browser"
    label = "手机模式浏览器"
    explain = "上一种被拦时，用浏览器模拟一台 iPhone 打开页面（全新的临时浏览器，不登录），读同样的页面数据。"
    needs = "visitor"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise StrategyFailed("没装 Playwright") from None
        at.log("启动模拟 iPhone 的临时浏览器（不登录，用完即删）", level="info")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not ctx.options.headed_browser)
            try:
                bctx = browser.new_context(**p.devices["iPhone 13"], locale="zh-CN")
                page = bctx.new_page()
                page.goto(ctx.url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(3000)
                at.log(f"最终页面：{page.url[:160]}")
                _check_blocked(page.url)
                state = page.evaluate("() => JSON.parse(JSON.stringify(window.__INITIAL_STATE__ || null))")
            finally:
                browser.close()
        note = _note_from_state(state)
        if not note:
            raise StrategyFailed(DEAD_LINK, {"reason": "浏览器里也没有笔记数据"})
        _remember(ctx, note)
        _download_from_note(ctx, at, note, self.key, "模拟 iPhone 的临时浏览器（没有登录）")


def _download_from_note(ctx: Ctx, at: AttemptCtx, note: dict, key: str, how: str) -> None:
    if note.get("type") != "video":
        raise StrategyFailed("这条笔记不是视频（是图文笔记），没有声音可以转写", final=True)
    streams = _streams(note)
    if not streams:
        raise StrategyFailed("笔记数据里没有视频地址")
    s = streams[0]
    at.log(f"共 {len(streams)} 档清晰度，选最小的：{s.get('videoCodec')} {s.get('width')}x{s.get('height')} "
           f"{(s.get('size') or 0) / 1e6:.1f}MB", level="info")
    out = _dl_dir(ctx, key)
    errors = []
    for url in [s["masterUrl"], *(s.get("backupUrls") or [])]:
        try:
            path = download_url(url, out / "media.mp4", at)
            ensure_ok_media(path, at)
        except StrategyFailed as exc:
            errors.append(exc.reason)
            continue
        ctx.media_path, ctx.media_strategy = path, key
        ctx.media_how = how
        at.success(f"下载好了：{path.name}（{path.stat().st_size / 1e6:.1f}MB）")
        return
    raise StrategyFailed("视频地址都下载失败：" + "；".join(errors[:2]))


class XhsYtdlp(Strategy):
    key = "xhs_ytdlp"
    label = "yt-dlp 下载（备用）"
    explain = "yt-dlp 也认识小红书（要用完整链接，不认短链接），只下最小的那档。放在最后当备用。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        from .. import ytdlp

        out = _dl_dir(ctx, self.key)
        path = ytdlp.download_media(ctx.url, out, at, audio_format="wa/w")
        ctx.remember_info(ytdlp.read_info_json(out))
        ensure_ok_media(path, at)
        ctx.media_path, ctx.media_strategy = path, self.key
        ctx.media_how = "yt-dlp 直接下载（没用 cookie）"
        at.success(f"下载好了：{path.name}")


class Xiaohongshu(Platform):
    key = "xiaohongshu"
    label = "小红书"
    hosts = ("xiaohongshu.com",)
    short_hosts = ("xhslink.com",)
    id_patterns = (r"/explore/([0-9a-f]{24})", r"/discovery/item/([0-9a-f]{24})", r"/item/([0-9a-f]{24})")
    collection_patterns = (r"xiaohongshu\.com/user/profile",)
    no_subtitle_reason = "小红书的字幕要先读页面数据才知道有没有。"

    def expand_short(self, url: str, log) -> str | None:
        """xhslink.com 短链接：不跟随跳转，直接读 302 的 Location（里面带 xsec_token）。"""
        with httpx.Client(headers={"User-Agent": MOBILE_UA}, timeout=15) as client:
            r = client.get(url, follow_redirects=False)
        loc = r.headers.get("location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            log(f"短链接没有跳转（状态 {r.status_code}）")
            return None
        loc = str(httpx.URL(url).join(loc))
        log(f"短链接展开（已去掉分享人的跟踪参数）：{self.canonical_url(loc, self.video_id(loc))}")
        return loc

    def canonical_url(self, url: str, video_id: str | None) -> str:
        """只保留 xsec_token / xsec_source（必须的），其他分享跟踪参数（分享人 ID、时间等）都去掉。"""
        u = httpx.URL(url)
        params = [(k, v) for k, v in u.params.multi_items() if k in ("xsec_token", "xsec_source")]
        base = str(u.copy_with(query=None, fragment=None))
        return str(httpx.URL(base).copy_merge_params(params)) if params else base

    def url_warning(self, url: str) -> str | None:
        if "xsec_token" not in url:
            return "链接里没有 xsec_token，小红书很可能不让看。最好从 App 里点「分享 → 复制链接」再贴进来。"
        return None

    def subtitle_strategies(self) -> list[Strategy]:
        return [XhsSubtitles()]

    def media_strategies(self) -> list[Strategy]:
        return [XhsMobileHttp(), XhsMobileBrowser(), XhsYtdlp()]
