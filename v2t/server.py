"""本地网页服务（只监听 127.0.0.1）：网页界面 + 接口 + 实时日志推送。"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, doctor, jobs
from .media import AUDIO_EXTS, VIDEO_EXTS
from .pipeline import make_plan, parse_input, run_job

app = FastAPI(title="v2t 视频转文字")

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@app.middleware("http")
async def _only_local_pages(request: Request, call_next):
    """只接受本机这个页面发来的请求：防止你浏览的其他网站（包括本机其他端口的网页）偷偷调用这个服务。"""
    host_hdr = request.headers.get("host") or ""
    host = host_hdr.rsplit(":", 1)[0]
    if host not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "只允许从本机访问"}, status_code=403)
    origin = request.headers.get("origin")
    # Origin 必须和这个服务自己的地址完全一样（协议 + 主机 + 端口）；"null" 也拒绝
    if origin and origin != f"http://{host_hdr}":
        return JSONResponse({"detail": "不接受其他网站发来的请求"}, status_code=403)
    site = request.headers.get("sec-fetch-site")
    if site in ("cross-site", "same-site"):
        # 唯一例外：从别处点链接 / 书签打开首页
        if not (request.url.path == "/" and request.headers.get("sec-fetch-mode") == "navigate"):
            return JSONResponse({"detail": "不接受其他网站发来的请求"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"  # 本机脚本很小；每次重载都取当前文件，避免旧 JS 和新 HTML 混用
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


_queue: "queue.Queue[str]" = queue.Queue()
_state = {"current": None}
_doctor_cache: dict = {"t": 0.0, "data": None}
_transcribe_lock = threading.Lock()


# ---------------------------------------------------------------- 后台工作线程
def _worker() -> None:
    while True:
        job_id = _queue.get()
        _state["current"] = job_id
        try:
            run_job(jobs.Job.load(job_id))
        except Exception as exc:  # 理论上 run_job 自己会兜底
            print("worker error", exc)
        finally:
            _state["current"] = None


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    # 上次服务关掉时还没跑完的任务：排队中的重新排队，跑到一半的标记失败
    for item in sorted(config.JOBS_DIR.glob("*/job.json")):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") == "running":
            pid = data.get("runner_pid")
            if pid and pid != os.getpid() and _alive(pid):
                continue  # 别的进程（比如命令行）正在跑它，不要动
            job = jobs.Job.load(data["id"])
            job.update(status="failed", error="服务中途重启，任务被打断了（可以点「重试」）")
            job.emit("job_end", "服务中途重启，任务被打断了", level="error", status="failed")
        elif data.get("status") == "queued" and data.get("runner") == "server":
            _queue.put(data["id"])
    threading.Thread(target=_worker, daemon=True).start()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------- 页面
@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


# ---------------------------------------------------------------- 接口
@app.get("/api/doctor")
def api_doctor(refresh: int = 0) -> dict:
    if refresh or not _doctor_cache["data"] or time.time() - _doctor_cache["t"] > 300:
        _doctor_cache["data"] = doctor.run_all(network=True, launch_browser=bool(refresh))
        _doctor_cache["t"] = time.time()
    return _doctor_cache["data"]


@app.get("/api/plan")
def api_plan(input: str, prefer_subtitles: bool = True, allow_thirdparty: bool = False,
             douyin_cookie_subtitles: bool = False, douyin_chrome: bool = False, keep_video: bool = False,
             official_only: bool = False) -> dict:
    opts = config.Options.from_dict({"prefer_subtitles": prefer_subtitles, "allow_thirdparty": allow_thirdparty,
                                     "douyin_cookie_subtitles": douyin_cookie_subtitles,
                                     "douyin_chrome": douyin_chrome, "keep_video": keep_video,
                                     "official_only": official_only})
    try:
        return make_plan(input, opts)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/status")
def api_status() -> dict:
    return {"current": _state["current"], "queued": _queue.qsize()}


@app.get("/api/chrome/status")
def chrome_status() -> dict:
    from .chrome_bridge import broker
    return broker.status()


@app.post("/api/chrome/{action}")
async def chrome_action(action: str, request: Request):
    from .chrome_bridge import broker
    if action == 'open':
        subprocess.Popen(['open', '-a', 'Google Chrome', f'http://{request.headers["host"]}/'])
        return {'ok': True}
    if action == 'folder':
        subprocess.Popen(['open', str(config.ROOT / 'chrome-connector')])
        return {'ok': True}
    if action not in ('heartbeat', 'claim', 'result'):
        raise HTTPException(404, '没有这个连接器操作')
    data = await request.body()
    if len(data) > 2_000_000:
        raise HTTPException(413, '连接器返回的资料过大')
    try:
        body = json.loads(data)
        if action == 'heartbeat':
            broker.heartbeat(body.get('client'))
            return {'ok': True}
        if action == 'claim':
            return broker.claim(body.get('client'))
        broker.receive(body.get('client'), body.get('token'), body.get('payload'))
        return {'ok': True}
    except (ValueError, TypeError, AttributeError, KeyError):
        raise HTTPException(400, '连接器资料无效或请求已结束') from None


@app.post("/api/jobs")
async def api_create_job(request: Request) -> dict:
    body = await request.json()
    try:
        kind, value = parse_input(body.get("input", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    options = config.Options.from_dict(body.get("options"))
    job = jobs.Job.create(value, options, kind)
    job.update(runner="server")
    job.emit("job_queued", "任务已排队" + (f"（前面还有 {_queue.qsize()} 个）" if _queue.qsize() else ""))
    _queue.put(job.id)
    return job.data


@app.get("/api/jobs")
def api_jobs(limit: int = 100) -> list[dict]:
    return jobs.list_jobs(limit)


def _job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[\w-]+", job_id):
        raise HTTPException(400, "bad id")
    path = config.JOBS_DIR / job_id
    if not (path / "job.json").exists():
        raise HTTPException(404, "没有这个任务")
    return path


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    return json.loads((_job_dir(job_id) / "job.json").read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/events")
def api_events(job_id: str, after: int = 0) -> list[dict]:
    return jobs.read_events(_job_dir(job_id), after)


@app.get("/api/jobs/{job_id}/stream")
async def api_stream(job_id: str, request: Request, after: int = 0) -> StreamingResponse:
    """Server-Sent Events：任务的每条新日志都实时推给网页。"""
    job_dir = _job_dir(job_id)

    async def gen():
        last = after
        idle_after_end = 0
        while True:
            if await request.is_disconnected():
                break
            events = jobs.read_events(job_dir, last)
            for ev in events:
                last = ev["seq"]
                yield f"id: {last}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            status = json.loads((job_dir / "job.json").read_text(encoding="utf-8")).get("status")
            if status in ("done", "failed"):
                idle_after_end += 1
                if idle_after_end > 2:
                    yield "event: end\ndata: {}\n\n"
                    break
            else:
                yield ": keepalive\n\n" if not events else ""
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


_SERVE_TYPES = {
    ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".srt": "text/plain; charset=utf-8", ".vtt": "text/plain; charset=utf-8",
    ".json": "application/json", ".jsonl": "text/plain; charset=utf-8", ".json3": "application/json",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".aac": "audio/aac", ".opus": "audio/ogg", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".flac": "audio/flac", ".webm": "video/webm", ".mp4": "video/mp4",
    ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".m4v": "video/mp4", ".m4s": "audio/mp4",
}


@app.get("/api/jobs/{job_id}/files/{name:path}")
def api_file(job_id: str, name: str) -> FileResponse:
    """只提供文字稿、记录和音视频这几类文件；其他类型（比如网页、图片）一律不给，防止被当成网页执行。"""
    job_dir = _job_dir(job_id).resolve()
    path = (job_dir / name).resolve()
    media_type = _SERVE_TYPES.get(path.suffix.lower())
    if job_dir not in path.parents or not path.is_file() or not media_type:
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path, media_type=media_type,
                        headers={"Content-Security-Policy": "sandbox; default-src 'none'"})


@app.post("/api/jobs/{job_id}/retry")
async def api_retry(job_id: str, request: Request) -> dict:
    """重试：默认用原来的选项；网页上改过的选项（比如打开了第三方解析）会覆盖进去。"""
    data = api_job(job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    opts = {**(data.get("options") or {}), **((body or {}).get("options") or {})}
    job = jobs.Job.create(data["input"], config.Options.from_dict(opts), data["input_type"])
    job.update(runner="server")
    job.emit("job_queued", f"重试任务 {job_id}")
    _queue.put(job.id)
    return job.data


@app.post("/api/jobs/{job_id}/transcribe")
def api_transcribe_downloaded(job_id: str) -> dict:
    """用已下载的视频新建本机转录任务，不再访问平台。"""
    with _transcribe_lock:
        source = jobs.Job.load(_job_dir(job_id).name)
        data = source.data
        result = data.get("result") or {}
        if data.get("status") != "done" or not data.get("options", {}).get("official_only") or result.get("source") != "platform_assets" or result.get("segments"):
            raise HTTPException(409, "这条任务不是已完成且没有原字幕的原文件任务")
        existing = data.get("transcription_job_id")
        if existing and (config.JOBS_DIR / existing / "job.json").is_file():
            return jobs.Job.load(existing).data
        rel = (result.get("files") or {}).get("media")
        if not isinstance(rel, str):
            raise HTTPException(409, "这条任务没有可转录的视频文件")
        media = (source.dir / rel).resolve()
        if source.dir.resolve() not in media.parents or not media.is_file() or media.suffix.lower() not in VIDEO_EXTS | AUDIO_EXTS:
            raise HTTPException(409, "已下载的视频文件找不到了")
        opts = config.Options.from_dict({**data.get("options", {}), "official_only": False,
                                         "prefer_subtitles": False, "douyin_chrome": False,
                                         "douyin_cookie_subtitles": False, "allow_thirdparty": False,
                                         "keep_video": True})
        job = jobs.Job.create(str(media), opts, "file")
        target = job.dir / ("source_media" + media.suffix.lower())
        try:
            try:
                os.link(media, target)
            except OSError:
                shutil.copy2(media, target)
            job.update(input=str(target), runner="server", source_job_id=job_id,
                       source_url=data.get("url"), source_title=data.get("title"),
                       source_platform_label=data.get("platform_label"),
                       source_platform_summary=result.get("platform_summary"))
            source.update(transcription_job_id=job.id)
            job.emit("job_queued", f"用任务 {job_id} 已下载的视频在本机转录，不重新下载")
            _queue.put(job.id)
        except Exception:
            shutil.rmtree(job.dir, ignore_errors=True)
            raise
        return job.data


@app.post("/api/jobs/{job_id}/reveal")
def api_reveal(job_id: str) -> dict:
    """在访达里打开这个任务的文件夹。"""
    path = _job_dir(job_id)
    target = path / "transcript.md"
    subprocess.Popen(["open", "-R", str(target if target.exists() else path)])
    return {"ok": True, "path": str(path)}


@app.delete("/api/jobs/{job_id}")
def api_delete(job_id: str) -> dict:
    path = _job_dir(job_id)
    if _state["current"] == job_id:
        raise HTTPException(409, "任务正在运行，不能删除")
    data = api_job(job_id)
    shutil.rmtree(path)
    # 如果是网页拖进来的文件，顺手删掉那份上传的副本
    src = Path(data.get("input") or "")
    if data.get("input_type") == "file" and config.UPLOADS_DIR.resolve() in src.resolve().parents:
        src.unlink(missing_ok=True)
    return {"ok": True}


@app.put("/api/upload")
async def api_upload(request: Request, name: str) -> dict:
    """网页拖进来的本地视频：存到 data/uploads/ 下，然后当本地文件处理。"""
    safe = re.sub(r"[^\w.\-\u4e00-\u9fff ]", "_", Path(name).name)[:120] or "upload.mp4"
    config.ensure_dirs()
    length = int(request.headers.get("content-length") or 0)
    if length and shutil.disk_usage(config.UPLOADS_DIR).free < length + (1 << 30):
        raise HTTPException(507, "磁盘空间不够了，先清理一下再拖进来")
    dest = config.UPLOADS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}-{safe}"
    part = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with part.open("wb") as fh:
            async for chunk in request.stream():
                fh.write(chunk)
                size += len(chunk)
    except BaseException:
        part.unlink(missing_ok=True)  # 传到一半断了：不留半截文件
        raise
    if size == 0:
        part.unlink(missing_ok=True)
        raise HTTPException(400, "文件是空的")
    part.rename(dest)
    return {"path": str(dest), "size": size}


@app.exception_handler(Exception)
async def _errors(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": f"服务出错：{exc}"}, status_code=500)
