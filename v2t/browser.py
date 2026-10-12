"""「真浏览器」相关：用一个专门下载用、从不登录的浏览器打开页面。

- 每个平台一个独立的浏览器配置（data/browser-profile/<平台>），和你日常用的 Chrome 完全隔开；
- 只有访客身份，从不登录；cookie 只留在这个专用浏览器的配置目录里，不导出、不显示内容；
- 页面里能找到视频地址的地方：网络请求、og:video 标签、页面内嵌数据。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config
from .jobs import AttemptCtx, StrategyFailed

# 出现这些名字说明是「登录 cookie」—— 我们不应该有，出现了要提醒
LOGIN_COOKIE_NAMES = {
    "douyin": {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"},
    "bilibili": {"SESSDATA", "bili_jct", "DedeUserID"},
    "xiaohongshu": {"web_session"},
    "youtube": {"SID", "SAPISID", "__Secure-1PSID", "LOGIN_INFO"},
}


_UA_CACHE: dict = {}

# 隐藏「我是自动化程序」的标记：不是为了破解什么，只是让页面像对普通访客一样正常加载
STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


def matching_ua(p) -> str:
    """User-Agent 的 Chrome 大版本号要和自带 Chromium 一致，不一致容易被风控盯上。"""
    if "ua" not in _UA_CACHE:
        try:
            b = p.chromium.launch(channel="chromium")
            major = b.version.split(".")[0]
            b.close()
        except Exception:
            major = "139"
        _UA_CACHE["ua"] = config.DESKTOP_UA.replace("Chrome/139.0.0.0", f"Chrome/{major}.0.0.0")
    return _UA_CACHE["ua"]


@contextmanager
def browser_page(platform_key: str, headed: bool = False, attempt: AttemptCtx | None = None,
                 extra_context: dict | None = None) -> Iterator:
    """打开一个持久化的、只属于这个平台的专用浏览器（默认不显示窗口，从不登录）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise StrategyFailed("没装 Playwright 浏览器组件") from None

    profile = config.BROWSER_PROFILES_DIR / platform_key
    fresh = not profile.exists()
    profile.mkdir(parents=True, exist_ok=True)
    if attempt:
        attempt.log(f"启动专用浏览器（{'显示窗口' if headed else '后台无窗口'}，"
                    f"{'全新的访客身份' if fresh else '沿用之前的访客身份'}），配置目录：{profile}")
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile),
                channel="chromium",
                headless=not headed,
                user_agent=matching_ua(p),
                locale="zh-CN",
                viewport={"width": 1366, "height": 860},
                args=["--disable-blink-features=AutomationControlled", "--autoplay-policy=no-user-gesture-required"],
                **(extra_context or {}),
            )
            ctx.add_init_script(STEALTH_JS)
            _drop_login_cookies(ctx, attempt)
            if headed and attempt:
                attempt.log("会弹出一个浏览器窗口，用完自动关闭。请不要在这个窗口里登录或扫码。", level="warn")
        except Exception as exc:
            msg = str(exc)
            reason = "浏览器启动失败"
            if "ProcessSingleton" in msg or "SingletonLock" in msg or "in use" in msg:
                reason = "专用浏览器正被另一个任务占用，稍后再试"
            elif "Executable doesn't exist" in msg:
                reason = "Playwright 的 Chromium 没装好（运行 .venv/bin/python -m playwright install chromium）"
            raise StrategyFailed(reason, {"error": msg[-800:]}) from None
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            yield ctx, page
        finally:
            ctx.close()


def _drop_login_cookies(ctx, attempt: AttemptCtx | None) -> None:
    """专用浏览器只能当访客：万一有人在里面登录过，打开时先把登录类 cookie 清掉。"""
    import re

    names = set().union(*LOGIN_COOKIE_NAMES.values())
    found = sorted({c["name"] for c in ctx.cookies() if c["name"] in names and c.get("value")})
    if not found:
        return
    ctx.clear_cookies(name=re.compile("^(" + "|".join(map(re.escape, sorted(names))) + ")$"))
    if attempt:
        attempt.log(f"专用浏览器里发现了登录类 cookie（{', '.join(found)}），已经清掉，这次仍然只当访客", level="warn")


def cookie_names(cookies: list[dict]) -> list[str]:
    return sorted({c["name"] for c in cookies})


def login_cookies_present(platform_key: str, cookies: list[dict]) -> list[str]:
    names = LOGIN_COOKIE_NAMES.get(platform_key, set())
    return sorted({c["name"] for c in cookies if c["name"] in names and c.get("value")})


def cookie_header(cookies: list[dict], host: str) -> str:
    pairs = []
    for c in cookies:
        dom = c.get("domain", "").lstrip(".")
        if dom and (host == dom or host.endswith("." + dom)):
            pairs.append(f"{c['name']}={c.get('value', '')}")
    return "; ".join(pairs)


def download_url(url: str, dest: Path, attempt: AttemptCtx, referer: str | None = None,
                 cookies: list[dict] | None = None, min_bytes: int = 50_000) -> Path:
    """用 httpx 直接下载一个媒体地址（带浏览器一样的 UA / Referer）。"""
    import httpx
    from urllib.parse import urlparse

    headers = {"User-Agent": config.DESKTOP_UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    if cookies:
        cookie = cookie_header(cookies, urlparse(url).hostname or "")
        if cookie:
            headers["Cookie"] = cookie
    attempt.log(f"直接下载：{url[:160]}{'…' if len(url) > 160 else ''}")
    t0 = time.time()
    size = 0
    try:
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=30) as resp:
            if resp.status_code >= 400:
                raise StrategyFailed(f"下载地址返回 {resp.status_code}（可能需要 Referer/cookie，或者链接过期）",
                                     {"http_status": resp.status_code})
            total = int(resp.headers.get("content-length") or 0)
            last = 0.0
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(1 << 16):
                    fh.write(chunk)
                    size += len(chunk)
                    if time.time() - last > 1.5:
                        last = time.time()
                        pct = f"{size * 100 // total}%" if total else f"{size // 1024}KB"
                        attempt.log(f"下载中… {pct}")
    except StrategyFailed:
        raise
    except Exception as exc:
        raise StrategyFailed(f"下载出错：{type(exc).__name__}", {"error": str(exc)[-500:]}) from None
    if size < min_bytes:
        raise StrategyFailed(f"下载到的文件太小（{size} 字节），不像是视频", {"size": size})
    if total and size < total:
        raise StrategyFailed(f"下载中断了（只下到 {size}/{total} 字节）", {"size": size, "total": total})
    attempt.log(f"下载完成：{size / 1e6:.1f}MB，用时 {time.time() - t0:.1f}s")
    return dest


MEDIA_HINTS = ("video/", "audio/")


def is_media_response(url: str, content_type: str, extra_patterns: tuple[str, ...] = ()) -> bool:
    ct = (content_type or "").lower()
    if ct.startswith(MEDIA_HINTS) and "mp2t" not in ct:
        return True
    return any(p in url for p in extra_patterns)
