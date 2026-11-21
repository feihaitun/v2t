"""网页一次运行一个任务；停止时结束下载、浏览器和转写子进程。"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import threading

from . import config, jobs


def terminate_tree(process: subprocess.Popen) -> None:
    groups = {process.pid}
    # 少数下载器会再创建独立进程组；只处理当前任务的后代。
    try:
        rows = subprocess.check_output(
            ['ps', '-axo', 'pid=,ppid=,pgid='], text=True, timeout=2).splitlines()
        records = [tuple(map(int, row.split())) for row in rows]
        descendants = {process.pid}
        while True:
            found = {pid for pid, parent, _ in records if parent in descendants}
            if found <= descendants:
                break
            descendants |= found
        groups |= {group for _, _, group in records if group in descendants}
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    for group in groups:
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # 主进程可能先退出，仍要收掉没有响应 TERM 的后代。
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=3)


class SingleTask:
    def __init__(self):
        self.lock = threading.RLock()
        self.current = None
        self.process = None
        self.cancelled = threading.Event()

    def start(self, job: jobs.Job) -> None:
        with self.lock:
            if self.current:
                raise RuntimeError('已有任务正在运行，请先停止它')
            parent, child = socket.socketpair()
            try:
                process = subprocess.Popen(
                    [config.PYTHON, '-m', 'v2t.job_worker', str(job.dir),
                     str(child.fileno()), str(os.getpid())],
                    cwd=config.ROOT, pass_fds=(child.fileno(),), start_new_session=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except BaseException:
                parent.close()
                raise
            finally:
                child.close()
            self.current, self.process = job.id, process
            self.cancelled = threading.Event()
            threading.Thread(target=self._watch, args=(job.id, process, parent, self.cancelled), daemon=True).start()

    def _watch(self, job_id, process, connection, cancelled):
        from .chrome_bridge import broker
        try:
            # 专用本机通道保留 Chrome 连接器；视频临时地址不写到磁盘或日志。
            with connection, connection.makefile('rwb') as channel:
                while line := channel.readline(4096):
                    request = json.loads(line)
                    video_id = request.get('video_id')
                    if not isinstance(video_id, str) or not re.fullmatch(r'\d{10,24}', video_id):
                        break
                    with self.lock:
                        if self.process is not process:
                            break
                    response = broker.request(video_id, cancelled=cancelled)
                    channel.write(json.dumps(response, ensure_ascii=False).encode() + b'\n')
                    channel.flush()
        except (OSError, ValueError):
            pass
        finally:
            process.wait()
            with self.lock:
                if self.process is process:
                    job = jobs.Job.load(job_id)
                    if job.data['status'] in ('queued', 'running'):
                        job.mark_interrupted('任务进程已退出，请重新开始')
                        job.emit('job_end', '任务进程已退出', level='error', status='failed')
                    self.current = self.process = None

    def stop(self, job_id: str) -> None:
        from .chrome_bridge import broker
        with self.lock:
            if self.current != job_id or self.process is None:
                return
            self.cancelled.set()
            broker.cancel()
            terminate_tree(self.process)
            job = jobs.Job.load(job_id)
            job.mark_interrupted('已停止本次任务')
            job.emit('job_end', '已停止，下载和转写进程已结束', status='failed')
            self.current = self.process = None
