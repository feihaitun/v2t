"""在独立子进程里跑 mlx-whisper（避免卡住网页服务），一边转一边打印进度行。

用法：python -m v2t.whisper_worker AUDIO OUT_JSON [--language zh] [--prompt "..."] [--model ...]
输出给父进程看的行：
  STATUS <文字>
  LANG <语言代码>
  SEG <开始秒> <结束秒> <文字>
  DONE <分段数>
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

# mlx-whisper verbose 输出的格式：[00:01.000 --> 00:04.500] 文字  或  [01:02:03.000 --> ...]
_LINE = re.compile(r"^\[((?:\d+:)?\d+:\d+\.\d+) --> ((?:\d+:)?\d+:\d+\.\d+)\]\s?(.*)$")

ZH_PROMPT = "以下是普通话的句子，使用简体中文，并加上逗号、句号等标点符号。"


def _to_sec(stamp: str) -> float:
    parts = [float(p) for p in stamp.split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


class _SegmentTap(io.TextIOBase):
    """截住 mlx-whisper 打印到 stdout 的每一行，转成 SEG 行给父进程。"""

    def __init__(self, real):
        self.real = real
        self.buf = ""

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            m = _LINE.match(line.strip())
            if m:
                self.real.write(f"SEG {_to_sec(m.group(1)):.2f} {_to_sec(m.group(2)):.2f} {m.group(3)}\n")
            elif line.strip().startswith("Detected language"):
                self.real.write(f"STATUS {line.strip()}\n")
            self.real.flush()
        return len(s)

    def flush(self) -> None:
        self.real.flush()


def read_wav(path: str):
    """直接读 16kHz 单声道 wav（主流程用 ffmpeg 准备好的），不依赖系统 PATH 里的 ffmpeg，也只解码一次。"""
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            return None  # 不是标准格式：交给 mlx-whisper 自己用 ffmpeg 读
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def detect_language(samples, model: str) -> str:
    """只取前 30 秒先认语言（几秒钟），决定要不要加中文提示词。"""
    import mlx_whisper
    import numpy as np

    clip = samples[: 16000 * 30].copy()
    if clip.size == 0 or float(np.abs(clip).max()) < 1e-4:
        return "unknown"
    result = mlx_whisper.transcribe(clip, path_or_hf_repo=model, verbose=None,
                                    temperature=0.0, condition_on_previous_text=False)
    return result.get("language") or "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("out_json")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    args = parser.parse_args()

    import mlx_whisper

    out = sys.stdout
    audio = read_wav(args.audio)
    if audio is None:
        from mlx_whisper.audio import load_audio
        import numpy as np

        audio = np.array(load_audio(args.audio), dtype=np.float32)
    language = None if args.language in ("", "auto") else args.language
    if language is None:
        out.write("STATUS 先听前 30 秒，判断是什么语言…\n")
        out.flush()
        language = detect_language(audio, args.model)
        if language == "unknown":
            language = None
    out.write(f"LANG {language or 'unknown'}\n")
    out.flush()

    prompt = args.prompt.strip()
    if language == "zh":
        prompt = (ZH_PROMPT + " " + prompt).strip()

    out.write("STATUS 开始转写全文…\n")
    out.flush()
    tap = _SegmentTap(out)
    with contextlib.redirect_stdout(tap):
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=args.model,
            language=language,
            initial_prompt=prompt or None,
            verbose=True,
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            condition_on_previous_text=True,
            word_timestamps=True,
            hallucination_silence_threshold=2.0,
        )

    segments = [
        {"start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2), "text": s["text"].strip()}
        for s in result.get("segments", [])
        if s.get("text", "").strip()
    ]
    Path(args.out_json).write_text(
        json.dumps({"language": result.get("language") or language, "model": args.model,
                    "prompt": prompt, "segments": segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out.write(f"DONE {len(segments)}\n")
    out.flush()


if __name__ == "__main__":
    main()
