"""几个平台都能用的「办法」：yt-dlp 读字幕、yt-dlp 直接下载、真浏览器抓视频地址。

注：曾经设想的「访客 cookie 交给 yt-dlp」实测（2026-10-11）对抖音无效（抖音每个请求要现场签名），
其他三个平台又根本不需要 cookie，所以没有做成单独的办法。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import ytdlp
from ..browser import browser_page, download_url, is_media_response
from ..jobs import AttemptCtx, StrategyFailed
from ..subtitles import parse_file
from .base import Ctx, Strategy, ensure_ok_media

def _dl_dir(ctx: Ctx, strategy_key: str) -> Path:
    path = ctx.workdir / f"download-{strategy_key}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class YtdlpSubtitles(Strategy):
    key = "ytdlp_subtitles"
    label = "读平台自带字幕（yt-dlp）"
    explain = "用 yt-dlp 读出视频信息，看有没有人工字幕或自动字幕；优先原语言，不要机器翻译的轨道。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        info = ytdlp.extract_info(ctx.url, at)
        ctx.remember_info(info)
        manual = sorted(k for k in (info.get("subtitles") or {}) if k not in ytdlp.NOT_SUBTITLES)
        auto = [k for k in (info.get("automatic_captions") or {}) if k.endswith("-orig")]
        at.log(f"人工字幕：{manual or '无'}；原语言自动字幕：{auto or '无'}", level="info")
        choice = ytdlp.pick_subtitle(info, ctx.options.language)
        if not choice:
            raise StrategyFailed("这个视频没有可用的字幕（没有人工字幕，也没有原语言的自动字幕）", expected=True)
        path = ytdlp.download_subtitle(info, choice, ctx.workdir, at)
        segments = parse_file(path)
        if len(segments) < 2:
            raise StrategyFailed("字幕文件是空的或者读不出来", {"file": path.name})
        kind = "人工字幕" if choice["kind"] == "manual" else "自动字幕"
        if choice["lang"].startswith("ai-"):
            kind = "AI 字幕"
        lang = choice["lang"].replace("ai-", "").replace("-orig", "")
        ctx.subtitle = {
            "segments": segments, "language": lang, "kind": choice["kind"], "track": choice["lang"],
            "file": path.name, "raw_file": path.name,
            "how": f"{ctx.platform.label} 自带的{kind}（轨道 {choice['lang']}）",
        }
        at.success(f"拿到{kind}：{choice['lang']}，{len(segments)} 段")


class YtdlpDirect(Strategy):
    key = "ytdlp_direct"
    label = "yt-dlp 直接下载"
    explain = "按“下载完整视频”选项取视频或音频，不带任何 cookie。"
    needs = "none"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        out = _dl_dir(ctx, self.key)
        path = ytdlp.download_media(ctx.url, out, at, keep_video=ctx.options.keep_video)
        ctx.remember_info(ytdlp.read_info_json(out))
        info = ensure_ok_media(path, at)
        if ctx.options.keep_video and not info["has_video"]:
            raise StrategyFailed("这次只拿到了音频，没有画面；接下来换办法下载完整视频")
        ctx.media_path, ctx.media_strategy = path, self.key
        ctx.media_how = "yt-dlp 直接下载（没用任何 cookie）"
        at.success(f"下载好了：{path.name}")


class BrowserGrab(Strategy):
    """真浏览器打开页面，从网络请求 / og:video / 页面内嵌数据里找视频直链，再直接下载。"""

    key = "browser_grab"
    label = "真浏览器抓视频地址"
    explain = ("像真人一样用浏览器打开页面（不登录），从页面源码和网络请求里找出视频文件的真实地址，"
               "再直接下载。最慢，但最不容易被拦。")
    needs = "visitor"

    def __init__(self, page_js: str, url_patterns: tuple[str, ...] = (), referer: str | None = None,
                 wait_ms: int = 6000, prefer_audio: bool = False):
        self.page_js = page_js
        self.url_patterns = url_patterns
        self.referer = referer
        self.wait_ms = wait_ms
        self.prefer_audio = prefer_audio

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        out = _dl_dir(ctx, self.key)
        seen: list[dict] = []
        with browser_page(ctx.platform.key, headed=ctx.options.headed_browser, attempt=at) as (bctx, page):
            def on_response(resp) -> None:
                try:
                    ct = resp.headers.get("content-type", "")
                    if is_media_response(resp.url, ct, self.url_patterns):
                        seen.append({"url": resp.url, "content_type": ct, "status": resp.status,
                                     "source": "网络请求"})
                except Exception:
                    pass

            page.on("response", on_response)
            at.log(f"浏览器打开：{ctx.url}", level="info")
            try:
                page.goto(ctx.url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as exc:
                raise StrategyFailed("浏览器打不开这个页面", {"error": str(exc)[-500:]}) from None
            page.wait_for_timeout(self.wait_ms)
            final_url = page.url
            title = ""
            try:
                title = page.title()
            except Exception:
                pass
            try:
                from_page = page.evaluate(self.page_js) or []
            except Exception as exc:
                from_page = []
                at.log(f"读页面数据出错：{exc}")
            cookies = bctx.cookies()
        if title and not ctx.info.get("title"):
            ctx.remember_info({"title": _clean_title(title)})
        page_items = []
        for item in from_page if isinstance(from_page, list) else []:
            if isinstance(item, dict) and item.get("url"):
                if item.get("title") or item.get("duration"):
                    ctx.remember_info({"title": item.get("title"), "duration": item.get("duration"),
                                       "uploader": item.get("author")})
                page_items.append({"url": item["url"], "source": item.get("source", "页面数据"),
                                   "kind": item.get("kind", "")})
        seen = page_items + seen  # 页面数据在前，并保持页面给的顺序（比如最小的音轨在最前）
        at.log(f"最终页面：{final_url}；找到 {len(seen)} 个候选地址", level="info",
               candidates=[{k: (v[:200] if isinstance(v, str) else v) for k, v in c.items()} for c in seen[:12]])
        candidates = self._rank(seen)
        if not candidates:
            raise StrategyFailed("页面里没找到视频地址（可能要登录、视频已删除，或者页面结构变了）",
                                 {"final_url": final_url, "title": title})
        errors = []
        for cand in candidates[:4]:
            if cand["url"].startswith("blob:"):
                continue
            ext = ".m4a" if cand.get("kind") == "audio" else ".mp4"
            dest = out / f"media{ext}"
            try:
                path = download_url(cand["url"], dest, at, referer=self.referer or final_url, cookies=cookies)
                info = ensure_ok_media(path, at)
                if ctx.options.keep_video and not info["has_video"]:
                    raise StrategyFailed("这个地址只提供音频，没有视频画面")
            except StrategyFailed as exc:
                errors.append(f"{cand['source']}：{exc.reason}")
                continue
            ctx.media_path, ctx.media_strategy = path, self.key
            ctx.media_how = f"真浏览器打开页面，从「{cand['source']}」里找到视频直链后直接下载（没有登录）"
            at.success(f"从「{cand['source']}」拿到文件：{path.name}")
            return
        raise StrategyFailed("找到了地址但都下载失败：" + "；".join(errors[:3]))

    def _rank(self, seen: list[dict]) -> list[dict]:
        unique, urls = [], set()
        for item in seen:
            if item["url"] in urls:
                continue
            urls.add(item["url"])
            unique.append(item)

        def score(item: dict) -> int:
            s = 0
            if item.get("source") != "网络请求":
                s -= 10  # 页面数据里的地址通常是完整文件，网络请求可能是分片
            if self.prefer_audio and item.get("kind") == "audio":
                s -= 5
            return s
        return sorted(unique, key=score)


def _clean_title(title: str) -> str:
    for suffix in (" - 抖音", " - 小红书", "_哔哩哔哩_bilibili", " - YouTube", " - 哔哩哔哩"):
        title = title.replace(suffix, "")
    return title.strip()


def dump_debug(ctx: Ctx, name: str, data) -> None:
    (ctx.workdir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
