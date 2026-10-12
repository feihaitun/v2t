"""把各种字幕文件（vtt / srt / YouTube json3 / B站 json）读成统一的分段列表。

分段格式：[{"start": 秒, "end": 秒, "text": "..."}]
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAG = re.compile(r"<[^>]+>")


def _ts_to_sec(text: str) -> float:
    m = _TS.search(text)
    if not m:
        return 0.0
    h = int(m.group(1) or 0)
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4).ljust(3, "0")) / 1000


def _clean(text: str) -> str:
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_vtt_or_srt(content: str, dedupe: bool | None = None) -> list[dict]:
    """dedupe=None 时自动判断：只有 YouTube 那种「滚动」自动字幕（带逐字时间标签）才去重，
    普通字幕原样保留（「对」「对对对」这种重复是真话，不能删）。"""
    if dedupe is None:
        dedupe = content.lstrip("\ufeff").startswith("WEBVTT") and bool(
            re.search(r"<\d{2}:\d{2}:\d{2}\.\d{3}>|<c>", content))
    segments: list[dict] = []
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_idx is None:
            continue
        start_s, _, end_s = lines[timing_idx].partition("-->")
        text_lines = [_clean(ln) for ln in lines[timing_idx + 1:]]
        text_lines = [ln for ln in text_lines if ln]
        if not text_lines:
            continue
        segments.append({"start": _ts_to_sec(start_s), "end": _ts_to_sec(end_s),
                         "text": " ".join(text_lines)})
    return dedupe_rolling(segments) if dedupe else segments


def dedupe_rolling(segments: list[dict]) -> list[dict]:
    """YouTube 自动字幕的 vtt 会「滚动」：同一句话出现两三次。这里把重复和前缀重叠去掉。"""
    out: list[dict] = []
    for seg in segments:
        text = seg["text"]
        if out:
            prev = out[-1]
            if text == prev["text"]:
                prev["end"] = max(prev["end"], seg["end"])
                continue
            # 新行以上一行开头 → 只保留新增部分
            if text.startswith(prev["text"]):
                extra = text[len(prev["text"]):].strip()
                if not extra:
                    continue
                text = extra
        out.append({**seg, "text": text})
    return [s for s in out if s["text"]]


def parse_json3(content: str) -> list[dict]:
    """YouTube json3：events[].segs[].utf8，tStartMs / dDurationMs。"""
    data = json.loads(content)
    segments = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000
        end = start + ev.get("dDurationMs", 0) / 1000
        segments.append({"start": start, "end": end, "text": text})
    return segments


def parse_bilibili_json(content: str) -> list[dict]:
    data = json.loads(content)
    body = data.get("body") if isinstance(data, dict) else None
    if not isinstance(body, list):
        return []
    return [
        {"start": float(i.get("from", 0)), "end": float(i.get("to", 0)), "text": _clean(i.get("content", ""))}
        for i in body if i.get("content")
    ]


def parse_file(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".json3":
        return parse_json3(content)
    if suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict) and "events" in data:
            return parse_json3(content)
        return parse_bilibili_json(content)
    return parse_vtt_or_srt(content)
