"""识别明确的字幕轨道及带时间轴的字幕；视频描述和章节摘要不参与解析。"""
from __future__ import annotations

import json
import math
from urllib.parse import urlparse

from ..subtitles import parse_vtt_or_srt

CDN_DOMAINS = ("douyin.com", "douyinvod.com", "douyinpic.com", "bytecdn.cn",
               "byteimg.com", "ibytedtos.com", "bytedance.com", "pstatp.com", "zjcdn.com")


def allowed_url(url: str) -> bool:
    try:
        u = urlparse(url)
        host = u.hostname or ""
        return (u.scheme == "https" and not u.username and not u.password and u.port in (None, 443)
                and any(host == d or host.endswith("." + d) for d in CDN_DOMAINS))
    except (ValueError, TypeError):
        return False


def language_code(track: dict) -> str:
    value = track.get("language") or track.get("lang") or track.get("LanguageCodeName") or "und"
    if isinstance(value, dict):
        value = value.get("code") or "und"
    return str(value).replace("_", "-").lower()


def candidate_tracks(detail: dict, want_language: str = "auto") -> list[dict]:
    video = detail.get("video") or {}
    groups = [detail.get("video_text"), video.get("subtitleInfos"),
              (video.get("cla_info") or {}).get("caption_infos")]
    for sticker in detail.get("interaction_stickers") or []:
        if isinstance(sticker, dict):
            groups.append((sticker.get("auto_video_caption_info") or {}).get("auto_captions"))
    out = []
    seen = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for track in group:
            if not isinstance(track, dict) or track.get("is_translated") or track.get("is_translate"):
                continue
            lang = language_code(track)
            if want_language != "auto" and lang.split("-")[0] != want_language.lower().split("-")[0]:
                continue
            if isinstance(track.get("utterances"), list):
                out.append({"language": lang, "data": track})
            url = track.get("url") or track.get("Url") or track.get("subtitle_url")
            urls = url.get("url_list", []) if isinstance(url, dict) else [url]
            if not url:
                urls = track.get("url_list") or []
            for url in urls if isinstance(urls, list) else []:
                if isinstance(url, str) and allowed_url(url) and url not in seen:
                    seen.add(url)
                    out.append({"language": lang, "url": url})
    return out[:12]


def valid_segments(segments) -> list[dict]:
    if not isinstance(segments, list):
        return []
    out = []
    for segment in segments[:20000]:
        try:
            start, end = float(segment["start"]), float(segment["end"])
            text = segment["text"]
            if (not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start
                    or not isinstance(text, str) or not text.strip()):
                continue
            out.append({"start": start, "end": end, "text": text.strip()})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda x: (x["start"], x["end"]))


def parse_payload(payload) -> list[dict]:
    if isinstance(payload, str):
        if len(payload) > 2_000_000:
            return []
        if "-->" in payload:
            return valid_segments(parse_vtt_or_srt(payload, dedupe=False))
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    if not isinstance(payload, dict) or not isinstance(payload.get("utterances"), list):
        return []
    out = []
    for cue in payload["utterances"]:
        try:
            out.append({"start": float(cue["start_time"]) / 1000,
                        "end": float(cue["end_time"]) / 1000, "text": cue["text"]})
        except (KeyError, TypeError, ValueError):
            continue
    return valid_segments(out)
