"""平台注册表：根据链接认出是哪个平台。新平台写好后加到 PLATFORMS 里就行。"""

from __future__ import annotations

from .base import Platform
from .bilibili import Bilibili
from .douyin import Douyin
from .generic import Generic
from .xiaohongshu import Xiaohongshu
from .youtube import YouTube

PLATFORMS: list[Platform] = [Douyin(), Xiaohongshu(), YouTube(), Bilibili()]
GENERIC = Generic()


def detect_platform(url: str) -> Platform:
    for platform in PLATFORMS:
        if platform.match(url):
            return platform
    return GENERIC
