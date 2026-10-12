"""任务记录：每个任务一个文件夹，job.json 记状态，events.jsonl 记后台每一步。

前端和命令行都只读这两个文件，所以不管任务是从网页、命令行还是 Claude 发起的，
都能在网页上看到同样的步骤和日志。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config

# 每一步的名字和大白话说明（前端「预计步骤」和步骤卡片都用这里的文字）
STEP_DEFS: dict[str, tuple[str, str]] = {
    "resolve": ("认链接", "判断是哪个平台，把短链接展开成完整地址，拿到视频编号和标题。"),
    "subtitles": ("找现成字幕", "先看平台自己有没有字幕（人工字幕或自动字幕）。有的话最快最准，后面就不用下载和转写了。"),
    "media": ("拿视频/音频", "把视频或音频文件下载到本地。会按顺序试几种办法，前一种被拦了就自动换下一种。"),
    "audio": ("抽音频", "用 ffmpeg 把声音单独抽出来，转成语音识别最喜欢的格式（16kHz 单声道 wav）。"),
    "transcribe": ("语音转文字", "用本机的 Whisper 模型（苹果芯片加速版）把说话内容转成带时间戳的文字，全程在本机完成。"),
    "output": ("整理输出", "生成 md / srt / txt / json 几种格式的文字稿，并记下这次是怎么拿到的。"),
}

NEEDS_LABELS = {
    "none": "不需要登录",
    "visitor": "用访客 cookie（不登录）",
    "login": "需要登录 cookie",
    "thirdparty": "会把链接发给第三方网站",
    "local": "只在本机",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StrategyFailed(Exception):
    """某一种办法没成功。reason 用大白话写，detail 放给开发模式看的原始信息。"""

    def __init__(self, reason: str, detail: dict | None = None, expected: bool = False, final: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}
        self.expected = expected  # True = 预料之中的「没有」（比如视频本来就没字幕），界面上不标红
        self.final = final        # True = 内容本身就拿不到（已删除 / 图文 / 私密），换办法也没用，直接停


class StepSkipped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class StepFailed(Exception):
    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


class Job:
    """一个任务。所有修改都会立刻写回磁盘。"""

    def __init__(self, job_dir: Path, data: dict):
        self.dir = job_dir
        self.data = data
        self._lock = threading.RLock()
        self._seq = self._count_events()
        self.listeners: list = []  # 命令行用：每条事件回调一次

    # ---------- 创建 / 读取 ----------
    @classmethod
    def create(cls, user_input: str, options: config.Options, input_type: str) -> "Job":
        config.ensure_dirs()
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        job_dir = config.JOBS_DIR / job_id
        job_dir.mkdir(parents=True)
        data = {
            "id": job_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "input": user_input,
            "input_type": input_type,
            "options": options.to_dict(),
            "status": "queued",
            "platform": None,
            "platform_label": None,
            "title": None,
            "author": None,
            "duration": None,
            "video_id": None,
            "url": None,
            "steps": [],
            "result": None,
            "error": None,
            "elapsed": None,
        }
        job = cls(job_dir, data)
        job.save()
        return job

    @classmethod
    def load(cls, job_id: str) -> "Job":
        job_dir = config.JOBS_DIR / job_id
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        return cls(job_dir, data)

    @property
    def id(self) -> str:
        return self.data["id"]

    # ---------- 持久化 ----------
    def save(self) -> None:
        with self._lock:
            self.data["updated_at"] = now_iso()
            tmp = self.dir / "job.json.tmp"
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.dir / "job.json")

    def update(self, **fields: Any) -> None:
        with self._lock:
            self.data.update(fields)
            self.save()

    def _count_events(self) -> int:
        path = self.dir / "events.jsonl"
        if not path.exists():
            return 0
        with path.open(encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    # ---------- 日志事件 ----------
    def emit(
        self,
        type_: str,
        msg: str,
        level: str = "info",
        step: str | None = None,
        strategy: str | None = None,
        extra: dict | None = None,
        **detail: Any,
    ) -> dict:
        """记一条事件。level: info（普通模式可见）/ debug（只在开发模式显示）/ warn / error。

        各种办法自带的细节放在 extra（一个字典）里传，不会和 status / level 这些参数名撞车。
        """
        detail = {**(extra or {}), **detail}
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": now_iso(),
                "t": round(time.time(), 3),
                "type": type_,
                "level": level,
                "step": step,
                "strategy": strategy,
                "msg": msg,
            }
            if detail:
                event["detail"] = detail
            with (self.dir / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # 回调出错不能影响任务
                pass
        return event

    # ---------- 步骤 ----------
    def step(self, key: str) -> "StepCtx":
        return StepCtx(self, key)

    def skip_step(self, key: str, reason: str) -> None:
        """直接把一步标成「跳过」（不经过开始），日志里只留一行。"""
        ctx = StepCtx(self, key)
        ctx.rec.update(status="skipped", summary=reason, started_at=now_iso(), ended_at=now_iso(), duration=0)
        self.save()
        self.emit("step_end", f"⏭ {ctx.rec['title']}：跳过 —— {reason}", step=key, status="skipped")

    def find_step(self, key: str) -> dict | None:
        for step in self.data["steps"]:
            if step["key"] == key:
                return step
        return None


def read_events(job_dir: Path, after: int = 0) -> list[dict]:
    path = job_dir / "events.jsonl"
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("seq", 0) > after:
                events.append(event)
    return events


def list_jobs(limit: int = 100) -> list[dict]:
    config.ensure_dirs()
    jobs = []
    for path in sorted(config.JOBS_DIR.glob("*/job.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        jobs.append(
            {
                k: data.get(k)
                for k in (
                    "id", "created_at", "input", "status", "platform", "platform_label",
                    "title", "duration", "error", "elapsed",
                )
            }
            | {"method": (data.get("result") or {}).get("method_short")}
        )
    return jobs


class StepCtx:
    """with job.step('media') as st: ... —— 自动记录开始、结束、成功、失败、跳过。"""

    def __init__(self, job: Job, key: str):
        self.job = job
        self.key = key
        title, explain = STEP_DEFS[key]
        existing = job.find_step(key)
        if existing is None:
            existing = {
                "key": key, "title": title, "explain": explain, "status": "pending",
                "started_at": None, "ended_at": None, "duration": None,
                "summary": None, "attempts": [],
            }
            job.data["steps"].append(existing)
        self.rec = existing
        self._t0 = 0.0

    def __enter__(self) -> "StepCtx":
        self._t0 = time.time()
        self.rec.update(status="running", started_at=now_iso())
        self.job.save()
        self.job.emit("step_start", f"▶ {self.rec['title']}：{self.rec['explain']}", step=self.key)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.rec["ended_at"] = now_iso()
        self.rec["duration"] = round(time.time() - self._t0, 2)
        if exc is None:
            if self.rec["status"] == "running":
                self.rec["status"] = "done"
            self.job.save()
            self.job.emit(
                "step_end", f"✔ {self.rec['title']}：{self.rec['summary'] or '完成'}",
                step=self.key, status=self.rec["status"], duration=self.rec["duration"],
            )
            return False
        if isinstance(exc, StepSkipped):
            self.rec.update(status="skipped", summary=exc.reason)
            self.job.save()
            self.job.emit("step_end", f"⏭ {self.rec['title']}：跳过 —— {exc.reason}",
                          step=self.key, status="skipped")
            return True
        if not isinstance(exc, Exception):  # Ctrl+C / 程序退出：原样往外抛，不当成「出错」
            self.rec.update(status="failed", summary="你手动停止了")
            self.job.save()
            self.job.emit("step_end", f"■ {self.rec['title']}：已手动停止", level="warn",
                          step=self.key, status="failed")
            return False
        reason = getattr(exc, "reason", None) or f"程序出错：{exc}"
        detail = getattr(exc, "detail", None) or {}
        if not isinstance(exc, (StepFailed, StrategyFailed)):
            detail = {**detail, "traceback": traceback.format_exc()}
        self.rec.update(status="failed", summary=reason)
        self.job.save()
        self.job.emit("step_end", f"✘ {self.rec['title']}失败：{reason}", level="error",
                      step=self.key, status="failed", extra=detail)
        if not isinstance(exc, StepFailed):
            raise StepFailed(reason, detail) from exc
        return False

    def summary(self, text: str) -> None:
        self.rec["summary"] = text
        self.job.save()

    def info(self, msg: str, level: str = "info", **detail: Any) -> None:
        self.job.emit("log", msg, level=level, step=self.key, extra=detail)

    def attempt(self, strategy) -> "AttemptCtx":
        return AttemptCtx(self, strategy)

    def record_skipped_attempt(self, strategy, reason: str) -> None:
        self.rec["attempts"].append(
            {**strategy.describe(), "status": "skipped", "message": reason,
             "started_at": None, "ended_at": None, "duration": None}
        )
        self.job.save()
        self.job.emit("attempt_end", f"  ⏭ {strategy.label}：{reason}", step=self.key,
                      strategy=strategy.key, status="skipped")


class AttemptCtx:
    """一次「试某种办法」。失败不会中断流程，只会记下来，然后换下一种办法。"""

    def __init__(self, step: StepCtx, strategy):
        self.step = step
        self.strategy = strategy
        self.rec = {**strategy.describe(), "status": "running", "message": None,
                    "started_at": None, "ended_at": None, "duration": None}
        self.ok = False
        self.final = False
        self._t0 = 0.0

    @property
    def job(self) -> Job:
        return self.step.job

    def __enter__(self) -> "AttemptCtx":
        self._t0 = time.time()
        self.rec["started_at"] = now_iso()
        self.step.rec["attempts"].append(self.rec)
        self.job.save()
        self.job.emit(
            "attempt_start", f"  → 试：{self.strategy.label} —— {self.strategy.explain}",
            step=self.step.key, strategy=self.strategy.key, needs=self.strategy.needs,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.rec["ended_at"] = now_iso()
        self.rec["duration"] = round(time.time() - self._t0, 2)
        if exc is None:
            self.ok = True
            self.rec["status"] = "ok"
            self.job.save()
            self.job.emit("attempt_end", f"  ✔ 成功：{self.rec['message'] or self.strategy.label}",
                          step=self.step.key, strategy=self.strategy.key, status="ok",
                          duration=self.rec["duration"])
            return False
        if not isinstance(exc, Exception):  # Ctrl+C：停下来，不再试下一种办法
            self.rec.update(status="failed", message="你手动停止了（Ctrl+C）")
            self.job.save()
            self.job.emit("attempt_end", "  ■ 已手动停止", level="warn", step=self.step.key,
                          strategy=self.strategy.key, status="failed")
            return False
        expected = False
        if isinstance(exc, StrategyFailed):
            reason, detail, expected, self.final = exc.reason, exc.detail, exc.expected, exc.final
        else:
            reason, detail = _plain_error(exc)
        status = "unavailable" if expected else "failed"
        self.rec.update(status=status, message=reason)
        if self.final:
            self.rec["final"] = True
        if detail:
            self.rec["detail"] = {k: v for k, v in detail.items() if k != "traceback"}
        try:
            self.job.save()
            if expected:
                self.job.emit("attempt_end", f"  · 没有：{reason}", level="info", step=self.step.key,
                              strategy=self.strategy.key, status=status, duration=self.rec["duration"],
                              extra=detail)
            else:
                self.job.emit("attempt_end", f"  ✘ 没成功：{reason}", level="warn", step=self.step.key,
                              strategy=self.strategy.key, status=status, duration=self.rec["duration"],
                              extra=detail)
        except Exception:  # 记日志出问题也不能挡住下一种办法
            pass
        return True  # 吞掉异常 → 继续下一种办法

    def success(self, message: str, **detail: Any) -> None:
        self.rec["message"] = message
        if detail:
            self.rec["detail"] = detail

    def log(self, msg: str, level: str = "debug", **detail: Any) -> None:
        self.job.emit("log", msg, level=level, step=self.step.key, strategy=self.strategy.key, extra=detail)


def _plain_error(exc: BaseException) -> tuple[str, dict]:
    """把常见的网络 / 浏览器异常翻译成大白话；原始信息留给开发模式。"""
    name, module = type(exc).__name__, type(exc).__module__ or ""
    detail = {"error": f"{name}: {str(exc)[-500:]}", "traceback": traceback.format_exc()}
    if module.startswith("httpx") or module.startswith("httpcore"):
        if "Timeout" in name:
            return "网络超时（网站太慢或网络不好），换下一种办法", detail
        return "网络连不上（断网、DNS 出错或网站拒绝连接），换下一种办法", detail
    if module.startswith("playwright"):
        if "Timeout" in name:
            return "浏览器等网页加载超时了，换下一种办法", detail
        return "浏览器打开网页时出错（网站可能改版了或打不开），换下一种办法", detail
    return f"程序出错：{name}: {exc}", detail
