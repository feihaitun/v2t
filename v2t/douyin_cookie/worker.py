"""Cookie 字幕探测子进程。stdout 仅返回字幕/白名单元数据，不返回凭据或原始响应。"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from urllib.parse import parse_qs, urljoin, urlparse

from .cookies import CookieUnavailable, load_cookies
from .tracks import allowed_url, candidate_tracks, parse_payload


def fetch_track(request, url, deadline):
    for _ in range(4):
        if not allowed_url(url) or time.monotonic() >= deadline:
            return []
        response = request.get(url, timeout=max(1, min(5000, (deadline - time.monotonic()) * 1000)),
                               max_redirects=0, headers={"Referer": "https://www.douyin.com/"})
        try:
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status != 200:
                return []
            if int(response.headers.get("content-length") or 0) > 2_000_000:
                return []
            body = response.body()
            return parse_payload(body.decode("utf-8-sig")) if len(body) <= 2_000_000 else []
        finally:
            response.dispose()
    return []


def probe(video_id: str, language: str, profile: str) -> dict:
    cookies = load_cookies(profile)
    from playwright.sync_api import sync_playwright
    from ..browser import STEALTH_JS
    from .. import config

    deadline = time.monotonic() + 23
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium", headless=True,
                                   args=["--disable-blink-features=AutomationControlled"])
        try:
            major = browser.version.split(".")[0]
            context = browser.new_context(locale="zh-CN", user_agent=config.DESKTOP_UA.replace(
                "Chrome/139.0.0.0", f"Chrome/{major}.0.0.0"))
            context.add_init_script(STEALTH_JS)
            context.add_cookies(cookies)
            cookies.clear()
            page = context.new_page()
            page.route("**/*", lambda route: route.abort() if route.request.resource_type == "media" else route.continue_())
            found = []

            def capture(response):
                u = urlparse(response.url)
                host = u.hostname or ""
                if (not (host == "douyin.com" or host.endswith(".douyin.com"))
                        or u.path != "/aweme/v1/web/aweme/detail/"
                        or parse_qs(u.query).get("aweme_id") != [video_id] or response.status != 200):
                    return
                try:
                    detail = response.json().get("aweme_detail")
                    if isinstance(detail, dict) and str(detail.get("aweme_id")) == video_id:
                        found.append(detail)
                except Exception:
                    pass

            page.on("response", capture)
            try:
                page.goto(f"https://www.douyin.com/video/{video_id}", wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            while not found and time.monotonic() < deadline - 5:
                page.wait_for_timeout(200)
            if not found:
                return {"status": "blocked"}
            detail = found[-1]
            for track in candidate_tracks(detail, language):
                try:
                    segments = (parse_payload(track["data"]) if "data" in track
                                else fetch_track(context.request, track["url"], deadline))
                except Exception:
                    continue
                if segments:
                    return {"status": "ok", "segments": segments, "language": track["language"], "meta": {
                        "title": detail.get("item_title") or detail.get("desc"),
                        "uploader": (detail.get("author") or {}).get("nickname"),
                        "duration": ((detail.get("video") or {}).get("duration") or 0) / 1000 or None,
                    }}
            return {"status": "no_subtitles"}
        finally:
            browser.close()


def main():
    try:
        # 第三方库的调试输出也不经过主流程的日志管道。
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = probe(*sys.argv[1:4])
    except CookieUnavailable as exc:
        result = {"status": exc.status}
    except Exception:
        result = {"status": "failed"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
