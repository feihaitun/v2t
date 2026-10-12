"""跑外部命令（yt-dlp / ffmpeg / whisper 子进程），每一行输出都记进日志（开发模式可见）。"""

from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from .jobs import Job

_PROGRESS = re.compile(r"^\[download\]\s+\d+(\.\d+)?%|^frame=|^size=")


class CmdResult:
    def __init__(self, cmd: list[str], returncode: int, lines: list[str], duration: float):
        self.cmd = cmd
        self.returncode = returncode
        self.lines = lines
        self.duration = duration

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    def tail(self, n: int = 15) -> str:
        return "\n".join(self.lines[-n:])


def pretty_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(str(c)) for c in cmd)


def run(
    cmd: list[str],
    job: Job | None = None,
    step: str | None = None,
    strategy: str | None = None,
    timeout: float = 1800,
    cwd: Path | None = None,
    on_line: Callable[[str], None] | None = None,
    keep_lines: int = 4000,
    log_lines: bool = True,
) -> CmdResult:
    """运行命令，合并 stdout/stderr，逐行记录；超过 timeout 秒会杀掉进程。"""
    cmd = [str(c) for c in cmd]
    if job:
        job.emit("cmd", f"$ {pretty_cmd(cmd)}", level="debug", step=step, strategy=strategy)
    t0 = time.time()
    lines: deque[str] = deque(maxlen=keep_lines)
    from . import config

    # 保证子进程（yt-dlp、Whisper）都能找到 ffmpeg，哪怕服务是从没有 Homebrew PATH 的地方启动的
    env = {**os.environ, "PATH": os.path.dirname(config.FFMPEG) + os.pathsep + os.environ.get("PATH", "")}
    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    q: queue.Queue = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            q.put(raw)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()

    timed_out = False
    finished_reading = False
    last_progress = 0.0
    while True:
        try:
            raw = q.get(timeout=0.5)
        except queue.Empty:
            raw = ""
        if raw is None:
            finished_reading = True
        elif raw:
            line = raw.rstrip("\n").rstrip()
            if line:
                lines.append(line)
                if on_line:
                    on_line(line)
                if job and log_lines:
                    is_progress = bool(_PROGRESS.search(line))
                    if not is_progress or time.time() - last_progress > 1.5:
                        job.emit("output", line, level="debug", step=step, strategy=strategy)
                        if is_progress:
                            last_progress = time.time()
        if finished_reading and proc.poll() is not None:
            break
        if time.time() - t0 > timeout:
            timed_out = True
            proc.kill()
            break
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    duration = round(time.time() - t0, 2)
    code = -9 if timed_out else proc.returncode
    if timed_out:
        lines.append(f"[超时 {int(timeout)} 秒，已停止]")
    if job:
        job.emit("cmd_end", f"  (退出码 {code}，用时 {duration}s)", level="debug",
                 step=step, strategy=strategy, returncode=code, duration=duration)
    return CmdResult(cmd, code, list(lines), duration)
