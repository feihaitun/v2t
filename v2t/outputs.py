"""把分段写成 md / srt / txt / json 四种文字稿。"""

from __future__ import annotations

import json
from pathlib import Path

from .media import human_duration

LANG_NAMES = {
    "zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文", "yue": "粤语", "fr": "法文",
    "de": "德文", "es": "西班牙文", "ru": "俄文", "unknown": "未知",
}


def lang_name(code: str | None) -> str:
    if not code:
        return "未知"
    base = code.split("-")[0].lower()
    return LANG_NAMES.get(base, code)


def _stamp(sec: float, srt: bool = False) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


_CJK = r"[\u3400-\u9fff\uf900-\ufaff]"
_PUNCT = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；"}


def zh_punct(text: str) -> str:
    """中文句子里的半角标点换成全角（数字里的逗号不动，比如 2,000）。"""
    import re

    for half, full in _PUNCT.items():
        text = re.sub(rf"(?<={_CJK})\s*{re.escape(half)}\s*|\s*{re.escape(half)}\s*(?={_CJK})", full, text)
    return text


def to_simplified(segments: list[dict]) -> list[dict]:
    """Whisper 有时会出繁体字，统一转成简体。"""
    try:
        import opencc
    except ImportError:
        return segments
    conv = opencc.OpenCC("t2s")
    return [{**s, "text": zh_punct(conv.convert(s["text"]))} for s in segments]


_END_PUNCT = "。！？.!?…，、；：,;:”」』）)"
_SENT_END = "。！？.!?…"


def plain_text(segments: list[dict], language: str | None, max_chars: int = 220) -> str:
    """不带时间的纯文字。

    - 中文不加空格；平台字幕常常一句一行、不带标点，就按停顿长短补上「，」或「。」；
    - 遇到长停顿、或者一段太长且刚好说完一句时分段；一段实在太长（超过两倍）也强制分段。
    """
    cjk = (language or "").startswith(("zh", "ja", "yue"))
    joiner = "" if cjk else " "
    paragraphs: list[str] = []
    current: list[str] = []
    size = 0
    last_end = None

    def close() -> None:
        nonlocal current, size
        if current:
            text = joiner.join(current)
            if cjk and text.endswith("，"):
                text = text[:-1] + "。"
            paragraphs.append(text)
        current, size = [], 0

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        gap = seg["start"] - last_end if last_end is not None else 0
        if current and cjk and current[-1][-1:] not in _END_PUNCT:
            current[-1] += "。" if gap > 0.8 else "，"
        ends_sentence = bool(current) and current[-1][-1:] in _SENT_END
        if current and (gap > 2.5 or (size > max_chars and ends_sentence) or size > 2 * max_chars):
            close()
        current.append(text)
        size += len(text)
        last_end = seg["end"]
    close()
    return "\n\n".join(paragraphs)


def write_all(out_dir: Path, segments: list[dict], meta: dict) -> dict[str, str]:
    """meta: title, url, platform_label, how (怎么拿到的), language, duration, source。"""
    files = {}
    language = meta.get("language")

    (out_dir / "transcript.json").write_text(
        json.dumps({**meta, "segments": segments}, ensure_ascii=False, indent=2), encoding="utf-8")
    files["json"] = "transcript.json"

    srt = []
    for i, seg in enumerate(segments, 1):
        srt += [str(i), f"{_stamp(seg['start'], True)} --> {_stamp(seg['end'], True)}", seg["text"], ""]
    (out_dir / "transcript.srt").write_text("\n".join(srt), encoding="utf-8")
    files["srt"] = "transcript.srt"

    text = plain_text(segments, language)
    (out_dir / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    files["txt"] = "transcript.txt"

    md = [
        f"# {meta.get('title') or '视频文字稿'}",
        "",
        f"- 来源：{meta.get('url') or meta.get('input') or '本地文件'}",
        f"- 平台：{meta.get('platform_label') or '—'}",
        f"- 时长：{human_duration(meta.get('duration'))}",
        f"- 语言：{lang_name(language)}",
        f"- 文字怎么来的：{meta.get('how') or '—'}",
        f"- 生成时间：{meta.get('generated_at') or ''}",
        "",
    ]
    summary = meta.get("platform_summary")
    if summary:
        md += [f"## {summary.get('source', '平台摘要')}", ""]
        if summary.get("abstract"):
            md += [summary["abstract"], ""]
        for ch in summary.get("chapters") or []:
            md.append(f"- **[{_stamp(ch.get('t') or 0)}] {ch.get('title') or ''}** {ch.get('detail') or ''}".rstrip())
        md.append("")
    md += [
        "## 全文",
        "",
        text,
        "",
        "## 带时间戳",
        "",
    ]
    md += [f"**[{_stamp(seg['start'])}]** {seg['text']}  " for seg in segments]
    (out_dir / "transcript.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    files["md"] = "transcript.md"
    return files
