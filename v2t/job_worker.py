"""网页任务的独立进程；由本机服务启动。"""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from . import config, jobs


def main():
    job_dir = Path(sys.argv[1])
    parent_pid = int(sys.argv[3])

    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(1)
        os.killpg(os.getpgrp(), signal.SIGTERM)

    threading.Thread(target=watch_parent, daemon=True).start()
    config.DATA = job_dir.parent.parent
    config.JOBS_DIR = job_dir.parent
    config.UPLOADS_DIR = config.DATA / 'uploads'
    config.BROWSER_PROFILES_DIR = config.DATA / 'browser-profile'
    from .chrome_bridge import broker
    from .pipeline import run_job
    with socket.socket(fileno=int(sys.argv[2])) as connection:
        connection.set_inheritable(False)
        with connection.makefile('rwb') as channel:
            def capture(video_id, timeout=35):
                channel.write(json.dumps({'video_id': video_id}).encode() + b'\n')
                channel.flush()
                return json.loads(channel.readline(8_000_000))
            broker.request = capture
            run_job(jobs.Job.load(job_dir.name))


if __name__ == '__main__':
    main()
