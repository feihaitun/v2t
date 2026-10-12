"""平台适配器的公共部分。

加一个新平台 = 写一个 Platform 子类，告诉系统三件事：
  1. 哪些链接归它管（hosts）、视频编号怎么从链接里取；
  2. 去哪找现成字幕（subtitle_strategies）；
  3. 怎么拿视频/音频，按顺序列出几种办法（media_strategies）。
后面的抽音频、转写、输出全部共用。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .. import config
from ..jobs import NEEDS_LABELS, AttemptCtx, Job, StrategyFailed


@dataclass
class Ctx:
    """一个任务运行时的上下文：各步骤之间传递的东西都在这里。"""

    job: Job
    options: config.Options
    workdir: Path
    platform: "Platform | None"
    input: str
    url: str | None = None
    video_id: str | None = None
    info: dict = field(default_factory=dict)          # 标题、时长、作者等
    media_path: Path | None = None
    media_how: str | None = None                      # 大白话：视频是怎么拿到的
    media_strategy: str | None = None
    media_label: str | None = None                    # 成功的那种办法的中文名
    subtitle: dict | None = None                      # {segments, language, kind, how}
    visitor_cookies: Path | None = None
    chrome_capture: dict | None = field(default=None, repr=False)  # 仅内存；不写入任务记录

    def remember_info(self, info: dict) -> None:
        """把 yt-dlp / 浏览器读到的标题、时长等合并进任务记录。"""
        if not info:
            return
        mapping = {
            "title": info.get("title") or info.get("fulltitle"),
            "author": info.get("uploader") or info.get("channel") or info.get("creator") or info.get("author"),
            "duration": info.get("duration"),
        }
        for key, value in mapping.items():
            if value and not self.info.get(key):
                self.info[key] = value
        if info.get("language") and not self.info.get("language"):
            self.info["language"] = info["language"]
        self.job.update(**{k: self.info.get(k) for k in ("title", "author", "duration")})


class Strategy:
    """一种「拿东西的办法」。run() 成功就返回，失败就抛 StrategyFailed（写清楚大白话原因）。"""

    key = ""
    label = ""
    explain = ""
    needs = "none"               # none / visitor / login / thirdparty
    option_flag: str | None = None   # 需要用户在选项里打开才会用（比如第三方网站）

    def describe(self) -> dict:
        return {
            "key": self.key, "label": self.label, "explain": self.explain,
            "needs": self.needs, "needs_label": NEEDS_LABELS.get(self.needs, self.needs),
            "optional": bool(self.option_flag),
        }

    def enabled(self, options: config.Options) -> tuple[bool, str]:
        if self.option_flag and not getattr(options, self.option_flag, False):
            return False, "默认关闭（要在选项里勾选才会用）"
        return True, ""

    def run(self, ctx: Ctx, at: AttemptCtx) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError


class Platform:
    key = "generic"
    label = "其他网站"
    hosts: tuple[str, ...] = ()
    short_hosts: tuple[str, ...] = ()
    id_patterns: tuple[str, ...] = ()
    no_subtitle_reason = "这个平台一般没有可以直接下载的字幕，直接走下载 + 转写。"

    def match(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.hosts + self.short_hosts)

    def is_short(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.short_hosts)

    def video_id(self, url: str) -> str | None:
        for pattern in self.id_patterns:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    def canonical_url(self, url: str, video_id: str | None) -> str:
        return url

    def expand_short(self, url: str, log) -> str | None:
        """平台自己的短链接展开办法；返回 None 表示用通用办法（HTTP 跟随跳转，不行再用浏览器）。"""
        return None

    def url_warning(self, url: str) -> str | None:
        """链接看起来有问题时给一句提醒（不阻止运行）。"""
        return None

    collection_patterns: tuple[str, ...] = ()

    def collection_reason(self, url: str) -> str | None:
        """频道 / 合集 / 播放列表 / 直播间这类「不是单个视频」的链接：直接说清楚，不去硬下。"""
        if any(re.search(p, url) for p in self.collection_patterns):
            return ("这是频道 / 主页 / 合集 / 播放列表 / 直播间的链接，不是单个视频。"
                    "请点开你想转的那个视频，复制它自己的链接再贴进来。")
        return None

    def subtitle_strategies(self) -> list[Strategy]:
        return []

    def media_strategies(self) -> list[Strategy]:
        return []

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label}


def wait_a_bit(seconds: float = 1.0) -> None:
    """对平台客气一点：两次请求之间稍微停一下。"""
    time.sleep(seconds)


def ensure_ok_media(path: Path, at: AttemptCtx) -> dict:
    from ..media import probe

    info = probe(path)
    if not info["has_audio"]:
        raise StrategyFailed("下载到的文件里没有声音轨道", {"probe": info})
    at.log(f"文件检查通过：{path.name}，时长 {info['duration'] or '?'} 秒，{info['codecs']}")
    return info
