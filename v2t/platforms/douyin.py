"""抖音：实测（2026-10-11）结论 ——

- 不登录就能下：用一个「专门的、从不登录」的浏览器打开视频页，让抖音页面自己发出带签名的请求，
  我们只是把页面拿到的视频详情读出来；
- 新的访客身份第一次打开时，抖音通常会拒绝（403 web_id_sign_invalid）—— 这是正常的「预热」，访客身份在这次访问里注册；
  关掉浏览器、用同一个访客身份再打开一次就会返回完整数据（200），冷启动约 7 秒。成功过一次的身份之后约 5 秒；
- 视频详情里有纯音频地址（几 MB），下载不需要 cookie；
- 「访客 cookie 交给 yt-dlp」不行：抖音每个请求都要现场计算签名（a_bogus 等），光有 cookie 没用；
- 默认访客流程不探测字幕，直接转写。可选的 Cookie 字幕模块由 pipeline 单独接入。
  页面数据里有时有「AI 章节摘要」，顺手存下来；摘要不是字幕。
"""

from __future__ import annotations

import json
import os
import shutil
import time

from .. import config
from ..browser import browser_page, download_url, login_cookies_present
from ..jobs import AttemptCtx, StrategyFailed
from .base import Ctx, Platform, Strategy, ensure_ok_media
from .common import YtdlpDirect, _dl_dir

DETAIL_PATH = "/aweme/v1/web/aweme/detail/"


def _one_visit(ctx: Ctx, at: AttemptCtx, attempt_no: int) -> tuple[dict | None, str]:
    """启动一次专用浏览器，打开一次视频页，读出页面自己请求到的视频详情。返回 (详情, 结果说明)。"""
    from playwright.sync_api import TimeoutError as PWTimeout

    vid = ctx.video_id
    url = f"https://www.douyin.com/video/{vid}"
    with browser_page("douyin", headed=ctx.options.headed_browser, attempt=at) as (bctx, page):
        at.log(f"第 {attempt_no} 次打开视频页，等页面自己去要视频详情…", level="info")
        try:
            # 匹配路径而不是域名：请求有时发到 www-hj.douyin.com
            with page.expect_response(lambda r: DETAIL_PATH in r.url and vid in r.url, timeout=20_000) as info:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            resp = info.value
            status, argus = resp.status, resp.headers.get("argus_security_code")
            body = resp.body()  # 必须在离开页面前读
        except PWTimeout:
            return None, "timeout"
        except Exception as exc:
            at.log(f"打开出错：{exc}", level="warn")
            return None, "error"
        at.log(f"视频详情请求返回 {status}，{len(body)} 字节" + (f"，风控代码 {argus}" if argus else ""),
               status=status, argus=argus)
        login = login_cookies_present("douyin", bctx.cookies())
        if login:
            at.log(f"注意：专用浏览器里有登录类 cookie（{', '.join(login)}）。我们不需要登录。", level="warn")
        if status == 200 and body:
            try:
                data = json.loads(body)
            except ValueError:
                data = {}
            if data.get("aweme_detail"):
                return data["aweme_detail"], "ok"
            at.log("返回 200 但没有视频数据", level="warn", filter=data.get("filter_detail"),
                   status_code=data.get("status_code"))
            return None, "empty"
        return None, f"{status}:{argus or ''}"


def _fetch_detail(ctx: Ctx, at: AttemptCtx, max_launches: int = 3) -> dict:
    """最多启动 3 次专用浏览器（每次只打开一次页面）。

    实测：同一个窗口里反复刷新不太稳；关掉浏览器、用同一个访客身份重新打开更可靠（冷启动约 7 秒）。
    """
    for n in range(1, max_launches + 1):
        detail, outcome = _one_visit(ctx, at, n)
        if detail:
            at.log(f"第 {n} 次：拿到视频详情 ✔", level="info")
            return detail
        if outcome == "empty":
            raise StrategyFailed("抖音说这个视频看不了：可能已删除、设为私密，或者在当前地区不可见", final=True)
        if outcome.startswith("403:web_id_sign_invalid") and n == 1:
            at.log("第 1 次：抖音拒绝了（403）—— 常见现象：新的访客身份要先「预热」一次才被认可。"
                   "关掉浏览器，用同一个访客身份再开一次", level="info")
        elif outcome == "timeout":
            at.log(f"第 {n} 次：20 秒内页面没发出视频详情请求（页面可能卡住了），重开浏览器再试", level="warn")
        else:
            at.log(f"第 {n} 次：被拒绝（{outcome}），重开浏览器再试", level="warn")
        time.sleep(2)
    ctx.info["douyin_need_fresh"] = True  # 真是被风控拒绝了：下一种办法可以换个全新的访客身份
    raise StrategyFailed("连续打开 3 次，抖音都没给视频数据（可能触发了风控，或者视频看不了）。"
                         "接下来会自动换一个全新的访客身份再试。")


def _remember(ctx: Ctx, detail: dict) -> None:
    video = detail.get("video") or {}
    ctx.remember_info({
        "title": detail.get("item_title") or detail.get("desc"),
        "uploader": (detail.get("author") or {}).get("nickname"),
        "duration": (video.get("duration") or detail.get("duration") or 0) / 1000 or None,
    })
    chapters = []
    abstract = detail.get("chapter_abstract")
    for item in detail.get("chapter_list") or []:
        chapters.append({"t": (item.get("timestamp") or 0) / 1000, "title": item.get("desc"),
                         "detail": item.get("detail")})
    rec = detail.get("recommend_chapter_info") or {}
    if not chapters and rec:
        abstract = abstract or rec.get("chapter_abstract")
        for item in rec.get("recommend_chapter_list") or []:
            chapters.append({"t": (item.get("timestamp") or 0) / 1000, "title": item.get("desc"),
                             "detail": item.get("detail")})
    if abstract or chapters:
        ctx.info["platform_summary"] = {"source": "抖音 AI 章节摘要（平台提供，不是逐字稿）",
                                        "abstract": abstract, "chapters": chapters}


def _media_urls(detail: dict, keep_video: bool = False) -> list[tuple[str, str]]:
    """(地址, 说明)：默认先纯音频（最小的），再最小的 mp4，最后 play_addr；选了「下载完整视频」就视频优先。"""
    video = detail.get("video") or {}
    audio_list = []
    audios = sorted(video.get("bit_rate_audio") or [],
                    key=lambda b: (b.get("audio_meta") or {}).get("size") or 1 << 60)
    for b in audios:
        ul = (b.get("audio_meta") or {}).get("url_list") or {}
        if isinstance(ul, dict):
            audio_list += [(ul[k], "纯音频") for k in ("main_url", "backup_url", "fallback_url") if ul.get(k)]
        else:
            audio_list += [(u, "纯音频") for u in ul]
    mp4 = [b for b in video.get("bit_rate") or [] if b.get("format") == "mp4" and b.get("play_addr")]
    if keep_video:  # 要完整视频时优先 H.264（H.265 在网页播放器里常常放不了），大小从小到大
        mp4.sort(key=lambda b: (b.get("is_h265") == 1, b["play_addr"].get("data_size") or 1 << 60))
    else:
        mp4.sort(key=lambda b: b["play_addr"].get("data_size") or 1 << 60)
    video_list = [(u, "mp4 视频") for b in mp4 for u in b["play_addr"].get("url_list") or []]
    video_list += [(u, "默认播放地址") for u in (video.get("play_addr") or {}).get("url_list") or []]
    return video_list + audio_list if keep_video else audio_list + video_list


class DouyinBrowserDetail(Strategy):
    key = "douyin_browser"
    label = "专用浏览器打开抖音页面（不登录）"
    explain = ("用一个从不登录的专用浏览器打开视频页，让抖音页面自己签名去要视频详情，我们只读结果。"
               "新的访客身份第一次常被拒（正常的预热），关掉重开就成功；预热过的身份之后约 5 秒。")
    needs = "visitor"

    def __init__(self, fresh_profile: bool = False):
        self.fresh_profile = fresh_profile
        if fresh_profile:
            self.key = "douyin_browser_fresh"
            self.label = "换一个全新的访客身份再试"
            self.explain = "清掉专用浏览器里的旧访客身份（可能被风控盯上了），重新预热后再打开一次。"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        if not ctx.video_id:
            raise StrategyFailed("没从链接里认出抖音视频编号（如果是图文笔记，没有声音可以转写）")
        if self.fresh_profile:
            if not ctx.info.get("douyin_need_fresh"):
                raise StrategyFailed("上一种办法不是被抖音风控拒绝的（比如视频本身的问题、浏览器没启动），"
                                     "换访客身份也没用，跳过", expected=True)
            profile = config.BROWSER_PROFILES_DIR / "douyin"
            if os.path.lexists(profile / "SingletonLock"):
                raise StrategyFailed("专用浏览器正被另一个任务使用，不能清掉它的访客身份，等那个任务结束再试")
            if profile.exists():
                old = profile.with_name("douyin.old")
                shutil.rmtree(old, ignore_errors=True)
                profile.rename(old)  # 先改名留一份，不直接删
                at.log("已把旧的访客身份换下来（留在 data/browser-profile/douyin.old），用全新的身份重试", level="info")
        t0 = time.time()
        detail = _fetch_detail(ctx, at)
        (ctx.workdir / "douyin_detail.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        _remember(ctx, detail)
        video = detail.get("video") or {}
        if detail.get("images") and not (video.get("bit_rate") or video.get("bit_rate_audio")):
            raise StrategyFailed("这是图文作品，没有视频声音可以转写", final=True)
        out = _dl_dir(ctx, self.key)
        errors = []
        wanted_video = ctx.options.keep_video
        for url, what in _media_urls(detail, wanted_video):
            dest = out / ("media.m4a" if what == "纯音频" else "media.mp4")
            try:
                path = download_url(url, dest, at, referer="https://www.douyin.com/")
                ensure_ok_media(path, at)
            except StrategyFailed as exc:
                errors.append(f"{what}：{exc.reason}")
                continue
            if wanted_video and what == "纯音频":
                at.log("视频地址都没下成，只拿到了纯音频", level="warn")
            ctx.media_path, ctx.media_strategy = path, self.key
            ctx.media_how = f"专用浏览器（不登录）打开抖音页面读到视频详情，下载了其中的{what}（没有用任何账号）"
            at.success(f"下载好了{what}：{path.stat().st_size / 1e6:.1f}MB，这一步共 {time.time() - t0:.0f} 秒")
            return
        raise StrategyFailed("拿到了视频详情，但地址都下载失败：" + "；".join(errors[:3]))


class DouyinThirdParty(Strategy):
    key = "douyin_thirdparty"
    label = "第三方解析网站（dlpanda.com）"
    explain = "最后的兜底：把链接交给第三方解析网站，让它返回无水印下载地址。会把链接发给别人的网站，默认关闭。"
    needs = "thirdparty"
    option_flag = "allow_thirdparty"

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:
        from ..browser import browser_page as _bp

        url = f"https://www.douyin.com/video/{ctx.video_id}" if ctx.video_id else ctx.url
        found: list[str] = []
        with _bp("thirdparty", headed=ctx.options.headed_browser, attempt=at) as (_bctx, page):
            at.log("打开 dlpanda.com，把链接贴进去", level="info")
            page.goto("https://dlpanda.com/", wait_until="domcontentloaded", timeout=45_000)
            box = page.locator("input[type=text], input[type=url], input[name=url]").first
            box.fill(url)
            box.press("Enter")
            page.wait_for_timeout(8000)
            found = page.evaluate("""() => Array.from(document.querySelectorAll('a[href], video[src], source[src]'))
                .map(e => e.href || e.src).filter(u => /douyinvod|zjcdn|aweme\\/v1\\/play|\\.mp4|\\.mp3/.test(u))""")
        at.log(f"解析网站返回 {len(found)} 个候选地址", level="info")
        out = _dl_dir(ctx, self.key)
        for u in found[:4]:
            try:
                path = download_url(u, out / ("media.mp3" if ".mp3" in u else "media.mp4"), at,
                                    referer="https://dlpanda.com/")
                ensure_ok_media(path, at)
            except StrategyFailed:
                continue
            ctx.media_path, ctx.media_strategy = path, self.key
            ctx.media_how = "第三方解析网站 dlpanda.com 给的下载地址（链接经过了别人的网站）"
            at.success(f"下载好了：{path.name}")
            return
        raise StrategyFailed("第三方解析网站没给出能下载的地址（网站可能改版了）")


class Douyin(Platform):
    key = "douyin"
    label = "抖音"
    hosts = ("douyin.com", "iesdouyin.com")
    short_hosts = ("v.douyin.com",)
    id_patterns = (r"/video/(\d{15,22})", r"modal_id=(\d{15,22})", r"/share/video/(\d{15,22})",
                   r"/note/(\d{15,22})")
    collection_patterns = (r"douyin\.com/user/", r"//live\.douyin\.com", r"douyin\.com/(collection|mix)/")
    no_subtitle_reason = "未启用抖音 Cookie 字幕探测，按原流程下载音频转写。"

    def canonical_url(self, url: str, video_id: str | None) -> str:
        return f"https://www.douyin.com/video/{video_id}" if video_id else url

    def url_warning(self, url: str) -> str | None:
        if "/note/" in url:
            return "这是图文作品链接，可能没有声音可以转写。"
        return None

    def media_strategies(self) -> list[Strategy]:
        ytdlp = YtdlpDirect()
        ytdlp.label = "yt-dlp 直接下载（备用）"
        ytdlp.explain = "2026-10 实测 yt-dlp 对抖音无效（缺签名会被拒）。放在后面，等 yt-dlp 哪天更新修好了还能用。"
        return [DouyinBrowserDetail(), DouyinBrowserDetail(fresh_profile=True), ytdlp, DouyinThirdParty()]
