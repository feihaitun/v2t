"""ffmpeg / ffprobe 相关：看文件信息、抽音频。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config
from .jobs import Job, StrategyFailed
from .runner import run

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".flv", ".m4v", ".avi", ".ts"}
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".webm"}


def probe(path: Path) -> dict:
    """返回 {duration, has_audio, has_video, size, format}；看不了就抛 StrategyFailed。"""
    try:
        proc = subprocess.run(
            [config.FFPROBE, "-v", "error", "-show_entries",
             "format=duration,size,format_name:stream=codec_type,codec_name",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise StrategyFailed("读取文件信息超时（文件可能有问题）") from None
    if proc.returncode != 0:
        raise StrategyFailed("这个文件不是能识别的音视频", {"ffprobe": proc.stderr[-800:]})
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    return {
        "duration": float(fmt.get("duration") or 0) or None,
        "size": int(fmt.get("size") or 0) or None,
        "format": fmt.get("format_name"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "codecs": [f"{s.get('codec_type')}:{s.get('codec_name')}" for s in streams],
    }


def extract_audio(src: Path, dst: Path, job: Job | None = None, step: str = "audio") -> Path:
    """抽成 16kHz 单声道 wav —— Whisper 直接吃这个，避开各种解码问题。"""
    result = run(
        [config.FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
        job=job, step=step, timeout=1800,
    )
    if not result.ok or not dst.exists() or dst.stat().st_size < 1000:
        raise StrategyFailed("ffmpeg 抽音频失败", {"ffmpeg_tail": result.tail(12)})
    return dst


def human_size(num: int | None) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"


def human_duration(seconds: float | None) -> str:
    if not seconds:
        return "?"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
