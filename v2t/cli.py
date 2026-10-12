"""命令行入口。

  ./v2t.sh run <链接或文件> [--lang zh] [--no-subs] [--thirdparty] [--dev]   转一个视频
  ./v2t.sh plan <链接或文件>                                              只看预计步骤
  ./v2t.sh doctor                                                       环境自检
  ./v2t.sh serve [--port 8765] [--no-open]                               打开网页界面
  ./v2t.sh jobs                                                        最近的任务
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser

from . import config, doctor, jobs
from .pipeline import make_plan, parse_input, run_job

ICON = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}


def _print_event(dev: bool):
    def show(ev: dict) -> None:
        if ev["type"] == "segment":
            if dev:
                print(f"      📝 [{ev['detail']['start']:.0f}s] {ev['msg']}")
            return
        if ev["level"] == "debug" and not dev:
            return
        prefix = f"[{ev['ts'][11:19]}] " if dev else ""
        line = ev["msg"]
        if dev and ev.get("detail") and ev["type"] not in ("output", "cmd", "cmd_end"):
            extra = {k: v for k, v in ev["detail"].items() if k != "traceback"}
            if extra:
                line += "  " + json.dumps(extra, ensure_ascii=False)[:400]
        print(prefix + line, flush=True)
    return show


def cmd_run(args: argparse.Namespace) -> int:
    try:
        kind, value = parse_input(args.input)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2
    options = config.Options(
        language=args.lang, prefer_subtitles=not args.no_subs, allow_thirdparty=args.thirdparty,
        keep_video=args.keep_video, initial_prompt=args.prompt or "", headed_browser=args.headed,
        douyin_cookie_subtitles=args.douyin_cookie_subtitles, douyin_cookie_profile=args.douyin_cookie_profile,
    )
    job = jobs.Job.create(value, options, kind)
    print(f"任务 {job.id}（网页上也能看到：http://127.0.0.1:{config.DEFAULT_PORT}/#job={job.id}）")
    job.listeners.append(_print_event(args.dev))
    try:
        run_job(job)
    except KeyboardInterrupt:
        print("\n已手动停止。")
        return 130
    if args.json:
        print(json.dumps(job.data, ensure_ascii=False, indent=2))
    if job.data["status"] == "done":
        files = job.data["result"]["files"]
        print("\n文字稿：")
        for key in ("md", "txt", "srt", "json"):
            if key in files:
                print(f"  {key:4} {job.dir / files[key]}")
        return 0
    print(f"\n❌ 失败：{job.data.get('error')}")
    return 1


def cmd_plan(args: argparse.Namespace) -> int:
    plan = make_plan(args.input, config.Options(allow_thirdparty=args.thirdparty,
                                               douyin_cookie_subtitles=args.douyin_cookie_subtitles))
    print(f"平台：{plan['platform']['label']}")
    for i, step in enumerate(plan["steps"], 1):
        print(f"\n{i}. {step['title']} —— {step['explain']}")
        if step.get("note"):
            print(f"   （{step['note']}）")
        for j, s in enumerate(step["strategies"], 1):
            flag = "" if s["enabled"] else f"  [{s['disabled_reason']}]"
            print(f"   {chr(96 + j)}) {s['label']}：{s['explain']}  〔{s['needs_label']}〕{flag}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    result = doctor.run_all(network=not args.offline, launch_browser=True)
    for c in result["checks"]:
        print(f"{ICON[c['status']]} {c['label']}：{c['detail']}")
        if c["status"] != "ok" and c.get("fix"):
            print(f"     修复：{c['fix']}")
    print("\n总体：" + {"ok": "全部正常", "warn": "能用，有几项要注意", "fail": "有问题，需要先修"}[result["status"]])
    return 0 if result["status"] != "fail" else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    import socket

    url = f"http://127.0.0.1:{args.port}/"
    try:
        socket.create_connection(("127.0.0.1", args.port), 0.3).close()
        print(f"网页服务已经在运行了：{url}")
        if not args.no_open:
            webbrowser.open(url)
        return 0
    except OSError:
        pass
    print(f"网页界面：{url}  （Ctrl+C 停止）")
    if not args.no_open:
        webbrowser.open(url)
    uvicorn.run("v2t.server:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def cmd_jobs(_args: argparse.Namespace) -> int:
    for j in jobs.list_jobs(30):
        status = {"done": "✅", "failed": "❌", "running": "⏳", "queued": "…"}.get(j["status"], j["status"])
        print(f"{status} {j['id']}  {j.get('platform_label') or '':6}  {(j.get('title') or j['input'])[:60]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="v2t", description="把视频链接或本地视频变成文字稿")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="转一个视频")
    p.add_argument("input", help="视频链接（可以是整段分享文案）或本地文件路径")
    p.add_argument("--lang", default="auto", help="语言：auto（自动）/ zh / en …")
    p.add_argument("--douyin-cookie-subtitles", action="store_true", help="用 Chrome 的抖音 Cookie 先尝试查字幕，失败自动转写")
    p.add_argument("--douyin-cookie-profile", default="", help="Chrome 用户配置目录名，默认最近使用的配置")
    p.add_argument("--no-subs", action="store_true", help="不用平台字幕，强制下载转写")
    p.add_argument("--thirdparty", action="store_true", help="允许用第三方解析网站兜底")
    p.add_argument("--keep-video", action="store_true", help="下载完整视频（默认只要音频）")
    p.add_argument("--prompt", help="给 Whisper 的提示词：人名、专业词")
    p.add_argument("--headed", action="store_true", help="浏览器步骤显示窗口（调试用）")
    p.add_argument("--dev", action="store_true", help="开发模式：打印所有命令和原始输出")
    p.add_argument("--json", action="store_true", help="结束时打印完整任务记录")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("plan", help="只看预计步骤，不运行")
    p.add_argument("input")
    p.add_argument("--douyin-cookie-subtitles", action="store_true")
    p.add_argument("--thirdparty", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("doctor", help="环境自检")
    p.add_argument("--offline", action="store_true", help="不检查网络")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("serve", help="打开网页界面")
    p.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("jobs", help="最近的任务")
    p.set_defaults(func=cmd_jobs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
