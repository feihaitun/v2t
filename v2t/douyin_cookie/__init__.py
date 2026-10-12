"""可选的抖音 Cookie 字幕模块。唯一入口：DouyinCookieSubtitles。

主流程仅在开关开启且平台为抖音时加载本模块。Cookie、浏览器和平台响应
留在有时限的子进程中；主流程只收到字幕及不含凭据的状态信息。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess

from .. import config
from ..jobs import StrategyFailed
from ..platforms.base import Strategy

PROBE_TIMEOUT = 35
REASONS = {
    "no_browser": "没找到 Chrome 用户配置，请先在 Chrome 打开并登录抖音",
    "bad_profile": "找不到指定的 Chrome 用户配置，请选择自动或填写 Default / Profile 1 这样的目录名",
    "cookie_unreadable": "无法读取 Chrome 的抖音 Cookie，请检查 Chrome 和系统钥匙串的访问权限",
    "no_login": "没有读到有效的抖音登录 Cookie，请在 Chrome 登录或刷新抖音后重试",
    "timeout": "Cookie 字幕探测超过 35 秒，已停止尝试",
    "blocked": "临时浏览器未捕获可用的视频详情（不能据此判断登录失效）；可改用网页上的已登录 Chrome 连接器",
    "no_subtitles": "这次登录会话没有拿到可用字幕（不代表画面没有文字）",
    "failed": "Cookie 字幕模块暂时不可用",
}


def run_probe(video_id: str, language: str, profile: str = "") -> dict:
    if not re.fullmatch(r"\d{15,22}", video_id or ""):
        return {"status": "blocked"}
    proc = None
    try:
        proc = subprocess.Popen(
            [config.PYTHON, "-B", "-m", "v2t.douyin_cookie.worker", video_id, language, profile],
            cwd=config.ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, text=True, start_new_session=True,
        )
        output, _ = proc.communicate(timeout=PROBE_TIMEOUT)
        if proc.returncode or len(output) > 4_000_000:
            return {"status": "failed"}
        result = json.loads(output)
        return result if isinstance(result, dict) else {"status": "failed"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception:
        # 不能把异常原文、命令输出或响应体交给公共任务日志。
        return {"status": "failed"}
    finally:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()


class DouyinCookieSubtitles(Strategy):
    key = "douyin_cookie_subtitles"
    label = "用 Chrome 的抖音 Cookie 查字幕"
    explain = ("每次重新读取所选 Chrome 用户配置中的抖音 Cookie，在临时浏览器中查找平台字幕。"
               "最多尝试 35 秒；没有字幕或失败就继续原来的访客下载和本机转写。")
    needs = "login"
    option_flag = "douyin_cookie_subtitles"

    def run(self, ctx, at):
        if not ctx.options.douyin_cookie_subtitles or not ctx.options.prefer_subtitles:
            raise StrategyFailed("Cookie 字幕模块已关闭", expected=True)
        at.log("正在重新读取 Chrome 的抖音会话并查字幕（只在本机使用，不记录 Cookie 内容）", level="info")
        result = run_probe(ctx.video_id, ctx.options.language, ctx.options.douyin_cookie_profile)
        status = result.get("status")
        if status != "ok":
            raise StrategyFailed(REASONS.get(status, REASONS["failed"]) + " → 继续原来的音频转写", expected=True)
        # 再做一次结构校验，防止异常响应被当成成功字幕。
        from .tracks import valid_segments

        segments = valid_segments(result.get("segments"))
        if not segments:
            raise StrategyFailed(REASONS["no_subtitles"] + " → 继续原来的音频转写", expected=True)
        ctx.remember_info(result.get("meta") or {})
        ctx.subtitle = {
            "segments": segments, "language": result.get("language") or "und",
            "kind": "platform", "how": "抖音平台字幕（使用 Chrome 的抖音 Cookie 获取）",
        }
        at.success(f"拿到抖音平台字幕：{len(segments)} 段；不需要下载音频和转写")
