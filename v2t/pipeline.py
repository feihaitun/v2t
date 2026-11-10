"""主流程：认链接 → 找现成字幕 → 拿视频/音频 → 抽音频 → 语音转文字 → 整理输出。

每一步都写进任务记录；拿东西的步骤会按顺序试几种办法，失败自动换下一种。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import config
from .jobs import Job, StepFailed, StepSkipped, StrategyFailed, now_iso
from .media import AUDIO_EXTS, VIDEO_EXTS, extract_audio, human_duration, probe
from .outputs import lang_name, to_simplified, write_all
from .platforms import detect_platform
from .platforms.base import Ctx, Platform
from .runner import run

# 链接里不会出现中文（中文会被编码成 %xx），所以遇到第一个非 ASCII 字符就截断
URL_RE = re.compile(r"https?://[^\s<>\"'\u0080-\U0010ffff]+")
# 分享文案里常见不带 https:// 的链接，比如 youtu.be/xxx、b23.tv/xxx
BARE_RE = re.compile(
    r"(?<![\w.])((?:www\.|m\.)?(?:youtube\.com|youtu\.be|bilibili\.com|b23\.tv|bili2233\.cn|douyin\.com|"
    r"v\.douyin\.com|iesdouyin\.com|xiaohongshu\.com|xhslink\.com)/[^\s<>\"'\u0080-\U0010ffff]+)")


# ---------------------------------------------------------------- 输入识别
def parse_input(raw: str) -> tuple[str, str]:
    """返回 (类型, 值)：('url', 链接) / ('file', 路径)。分享文案里夹着的链接也能认出来。"""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("输入是空的")
    candidate = raw.strip("'\"")
    path = Path(candidate).expanduser()
    if candidate.startswith(("/", "~", "./")) and path.exists():
        if not path.is_file():
            raise ValueError("这不是一个普通文件（只能转视频或音频文件）")
        return "file", str(path.resolve())
    m = URL_RE.search(raw)
    if m:
        return "url", m.group(0).rstrip(").,;!?）】")
    m = BARE_RE.search(raw)
    if m:
        return "url", "https://" + m.group(1).rstrip(").,;!?）】")
    if path.is_file():
        return "file", str(path.resolve())
    raise ValueError("没认出链接，也不是本机上存在的文件路径")


def make_plan(raw: str, options: config.Options | None = None) -> dict:
    """给前端「预计步骤」用：还没开始跑，先告诉你大概会经过哪些步骤、每步有哪几种办法。"""
    from .jobs import STEP_DEFS

    options = options or config.Options()
    kind, value = parse_input(raw)
    steps = []

    def add(key: str, strategies: list | None = None, note: str | None = None) -> None:
        title, explain = STEP_DEFS[key]
        steps.append({"key": key, "title": title, "explain": explain,
                      "strategies": strategy_dicts(strategies or [], options), "note": note})

    if kind == "file":
        if options.official_only:
            raise ValueError("平台原文件模式需要 B站、抖音、YouTube 或 X 的视频链接")
        info = {}
        try:
            info = probe(Path(value))
        except StrategyFailed:
            pass
        add("resolve", note=f"本地文件，时长 {human_duration(info.get('duration'))}")
        add("audio")
        add("transcribe")
        add("output")
        return {"input_type": "file", "value": value, "platform": {"key": "local", "label": "本地文件"},
                "steps": steps}

    platform = detect_platform(value)
    warning = None if platform.is_short(value) else (platform.collection_reason(value) or platform.url_warning(value))
    add("resolve", note="短链接会先展开" if platform.is_short(value) else None)
    subs = subtitle_strategies(platform, options)
    if not options.prefer_subtitles:
        add("subtitles", note="你选了「不用平台字幕，直接转写」，这一步会跳过")
    elif subs:
        add("subtitles", subs, note="找到字幕后仍会下载完整视频，跳过转写" if options.keep_video else "找到字幕的话，下面下载和转写都会跳过")
    else:
        add("subtitles", note=platform.no_subtitle_reason)
    add("media", media_strategies(platform, options), note="下载完整视频；按顺序试" if options.official_only else "按顺序试，前一种成功就不再试后面的")
    if options.official_only:
        add("audio", note="只要平台原文件，跳过抽音频")
        add("transcribe", note="只要平台原文件，不运行 Whisper")
        add("output", note="保存平台原字幕文件和完整视频；没有原字幕时仍保留视频")
    else:
        add("audio")
        add("transcribe")
        add("output")
    return {"input_type": "url", "value": value, "platform": platform.describe(), "steps": steps,
            "warning": warning}


def subtitle_strategies(platform: Platform, options: config.Options) -> list:
    """可选模块接入点：关掉开关或处理其他平台时，既不导入也不调用模块。"""
    strategies = platform.subtitle_strategies()
    if platform.key == "bilibili" and options.official_only:
        from .platforms.bilibili import BiliChromeSubtitles
        return [BiliChromeSubtitles()]
    if platform.key == "youtube" and options.official_only:
        from .platforms.youtube import YouTubeChromeSubtitles, YouTubeGuestSubtitles
        return [YouTubeChromeSubtitles(), YouTubeGuestSubtitles()]
    if platform.key == "douyin" and options.official_only:
        from .platforms.douyin import DouyinGuestSubtitles
        if options.douyin_chrome:
            from .chrome_bridge import ChromeSubtitles
            return [ChromeSubtitles(), DouyinGuestSubtitles()]
        return [DouyinGuestSubtitles()]
    if platform.key == "douyin" and options.douyin_chrome and options.prefer_subtitles:
        from .chrome_bridge import ChromeSubtitles
        return [ChromeSubtitles(), *strategies]
    if platform.key == "douyin" and options.douyin_cookie_subtitles and options.prefer_subtitles:
        from .douyin_cookie import DouyinCookieSubtitles
        return [DouyinCookieSubtitles(), *strategies]
    return strategies


def media_strategies(platform: Platform, options: config.Options) -> list:
    strategies = platform.media_strategies()
    if platform.key == "bilibili" and options.official_only:
        from .platforms.bilibili import BiliChromeMedia
        return [BiliChromeMedia(), strategies[0]]
    if platform.key == "youtube" and options.official_only:
        from .platforms.youtube import YouTubeChromeMedia
        return [YouTubeChromeMedia(), *strategies]
    if platform.key == "douyin" and options.official_only:
        strategies = [s for s in strategies if s.key != "douyin_thirdparty"]
    if platform.key == "douyin" and options.douyin_chrome:
        from .chrome_bridge import ChromeMedia
        return [ChromeMedia(), *strategies]
    return strategies


def strategy_dicts(strategies: list, options: config.Options) -> list[dict]:
    items = []
    for s in strategies:
        ok, why = s.enabled(options)
        items.append({**s.describe(), "enabled": ok, "disabled_reason": why})
    return items


# ---------------------------------------------------------------- 主流程
def run_job(job: Job) -> Job:
    options = config.Options.from_dict(job.data.get("options"))
    t0 = time.time()
    job.update(status="running", started_at=now_iso(), runner_pid=os.getpid())
    job.emit("job_start", f"开始处理：{job.data['input']}")
    ctx = Ctx(job=job, options=options, workdir=job.dir, platform=None, input=job.data["input"])
    try:
        kind = job.data["input_type"]
        if kind == "file":
            if options.official_only:
                raise StepFailed("平台原文件模式需要 B站、抖音、YouTube 或 X 的视频链接")
            _resolve_file(ctx)
            job.skip_step("subtitles", "本地文件没有平台字幕")
            job.skip_step("media", "文件已经在本机，不用下载")
        else:
            _resolve_url(ctx)
            if options.official_only and ctx.platform.key not in ("bilibili", "douyin", "youtube", "twitter"):
                raise StepFailed("平台原字幕和完整视频模式目前支持 B站、抖音、YouTube 和 X")
            _find_subtitles(ctx)
            if options.official_only:
                try:
                    _get_media(ctx)
                except StepFailed as exc:
                    if not ctx.subtitle:
                        raise
                    ctx.info["media_error"] = exc.reason
                job.skip_step("audio", "只要平台原文件，不抽音频")
                job.skip_step("transcribe", "只要平台原文件，不运行 Whisper")
            elif ctx.subtitle:
                if options.keep_video:
                    _get_media(ctx)
                else:
                    job.skip_step("media", "已经拿到现成字幕，不用再下载")
                for key in ("audio", "transcribe"):
                    job.skip_step(key, "已经拿到现成字幕，不用再下载和转写")
            else:
                _get_media(ctx)
        if not ctx.subtitle and not options.official_only:
            wav = _extract_audio(ctx)
            _transcribe(ctx, wav)
        _write_outputs(ctx, time.time() - t0)
        job.update(status="done", elapsed=round(time.time() - t0, 1))
        res = job.data["result"]
        if options.official_only:
            job.emit("job_end", f"完成！{res['how']}。总用时 {job.data['elapsed']} 秒。", status="done")
        else:
            job.emit("job_end", f"🎉 完成！{res['how']}。共 {res['segments']} 段、{res['chars']} 字，总用时 "
                                f"{job.data['elapsed']} 秒。", status="done")
    except StepFailed as exc:
        job.update(status="failed", error=exc.reason, elapsed=round(time.time() - t0, 1))
        job.emit("job_end", f"任务失败：{exc.reason}", level="error", status="failed")
    except KeyboardInterrupt:
        job.update(status="failed", error="你手动停止了任务", elapsed=round(time.time() - t0, 1))
        job.emit("job_end", "任务已手动停止", level="warn", status="failed")
        raise
    except Exception as exc:  # 兜底：程序本身出错
        import traceback

        job.update(status="failed", error=f"程序出错：{exc}", elapsed=round(time.time() - t0, 1))
        job.emit("job_end", f"任务失败（程序出错）：{exc}", level="error", status="failed",
                 traceback=traceback.format_exc())
    finally:
        _cleanup(ctx)
    return job


def _resolve_file(ctx: Ctx) -> None:
    job = ctx.job
    with job.step("resolve") as st:
        path = Path(job.data["input"])
        if not path.exists():
            raise StepFailed(f"找不到文件：{path}")
        if path.suffix.lower() not in VIDEO_EXTS | AUDIO_EXTS:
            st.info(f"文件后缀 {path.suffix} 不常见，试试看能不能读", level="warn")
        try:
            info = probe(path)
        except StrategyFailed as exc:
            raise StepFailed(exc.reason, exc.detail) from None
        if not info["has_audio"]:
            raise StepFailed("这个文件里没有声音，没法转文字")
        ctx.media_path = path
        from_download = bool(job.data.get("source_job_id"))
        ctx.media_how = "已经下载到本机的视频" if from_download else "本地文件"
        ctx.media_label = "已下载视频" if from_download else "本地文件"
        ctx.url = job.data.get("source_url") if from_download else None
        ctx.info.update(title=job.data.get("source_title") or path.stem, duration=info["duration"])
        if from_download:
            ctx.info["platform_summary"] = job.data.get("source_platform_summary")
        job.update(platform="local", platform_label=job.data.get("source_platform_label") or "本地文件",
                   title=ctx.info["title"], duration=info["duration"])
        st.summary(("复用已下载的视频" if from_download else f"本地文件 {path.name}") +
                   f"，时长 {human_duration(info['duration'])}")


def _resolve_url(ctx: Ctx) -> None:
    job = ctx.job
    with job.step("resolve") as st:
        url = job.data["input"]
        platform: Platform = detect_platform(url)
        ctx.platform = platform
        job.update(platform=platform.key, platform_label=platform.label)
        st.info(f"平台：{platform.label}")
        # 只有短链接、或者认识的平台却没认出视频编号时才去展开；陌生网站的普通链接不用
        if platform.is_short(url) or (platform.key != "generic" and not platform.video_id(url)):
            expanded = None
            try:
                expanded = platform.expand_short(url, st.info)
            except Exception as exc:
                st.info(f"平台专用的短链接展开失败：{exc}", level="debug")
            expanded = expanded or _expand_url(url, st)
            if expanded and expanded != url:
                url = expanded
                # 展开后可能换了平台（比如分享链接跳到别的域名）
                if detect_platform(url).key != "generic":
                    platform = detect_platform(url)
                ctx.platform = platform
                job.update(platform=platform.key, platform_label=platform.label)
        vid = platform.video_id(url)
        collection = platform.collection_reason(url)
        if collection and not vid:
            raise StepFailed(collection)
        warning = platform.url_warning(url)
        if warning:
            st.info(warning, level="warn")
        ctx.video_id = vid
        ctx.url = platform.canonical_url(url, vid)
        if ctx.url != job.data["input"]:
            st.info(f"整理后的链接（去掉了跟踪参数）：{ctx.url}", level="debug")
        job.update(url=ctx.url, video_id=vid)
        if platform.key != "generic" and not vid:
            st.info("没从链接里认出视频编号，后面照常尝试", level="warn")
        # 把这个平台接下来会按顺序试的办法记进任务，网页上没轮到的办法也能看到
        opts = ctx.options
        job.update(plan={
            "subtitles": strategy_dicts(subtitle_strategies(platform, opts), opts) if opts.prefer_subtitles else [],
            "media": strategy_dicts(media_strategies(platform, opts), opts),
        })
        st.summary(f"{platform.label}" + (f"，视频编号 {vid}" if vid else "") + f" → {ctx.url}")


def _short(url: str) -> str:
    """日志里只记 协议://域名/路径，不记 ? 后面的参数（里面常有分享人的跟踪信息）。"""
    from urllib.parse import urlsplit

    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}"


def _expand_url(url: str, st) -> str | None:
    """先用普通 HTTP 跟随跳转（只读响应头，不下载内容）；出错时再用真浏览器（有的短链靠网页脚本跳转）。"""
    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=15,
                          headers={"User-Agent": config.DESKTOP_UA}) as client:
            with client.stream("GET", url) as resp:
                final, status = str(resp.url), resp.status_code
        st.info(f"普通 HTTP 跳转：状态 {status} → {_short(final)}", level="debug")
        if status < 400:
            return final
    except Exception as exc:
        st.info(f"普通 HTTP 展开失败：{type(exc).__name__}", level="debug")
    try:
        from .browser import browser_page

        with browser_page(detect_platform(url).key) as (_bctx, page):
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2500)
            final = page.url
        st.info(f"用浏览器展开：{_short(final)}", level="debug")
        return final
    except Exception as exc:
        st.info(f"浏览器展开也失败：{type(exc).__name__}", level="warn")
        return None


def _find_subtitles(ctx: Ctx) -> None:
    job, options = ctx.job, ctx.options
    if not options.prefer_subtitles:
        job.skip_step("subtitles", "你选了「不用平台字幕，直接转写」")
        return
    strategies = subtitle_strategies(ctx.platform, options)
    if not strategies:
        job.skip_step("subtitles", ctx.platform.no_subtitle_reason)
        return
    with job.step("subtitles") as st:
        st.info("会按顺序试：" + " → ".join(s.label for s in strategies))
        for strategy in strategies:
            ok, why = strategy.enabled(options)
            if not ok:
                st.record_skipped_attempt(strategy, why)
                continue
            with st.attempt(strategy) as at:
                strategy.run(ctx, at)
            if at.ok and ctx.subtitle:
                st.summary(f"找到{ctx.subtitle['how']}，{len(ctx.subtitle['segments'])} 段")
                return
            if at.final:  # 内容本身拿不到（已删除 / 图文 / 私密），后面也不用试了
                raise StepFailed(at.rec.get("message") or "内容拿不到")
        last = next((a.get("message") for a in reversed(st.rec["attempts"]) if a.get("message")), None)
        if options.official_only:
            raise StepSkipped(f"{last or '没有可用的平台原字幕'} → 接下来下载完整视频，不转录")
        if last and "转写" in last:
            raise StepSkipped(last)
        raise StepSkipped(f"{last} → 接下来下载音频，自己转写" if last else "没有现成字幕 → 接下来下载音频，自己转写")


def _get_media(ctx: Ctx) -> None:
    job, options = ctx.job, ctx.options
    with job.step("media") as st:
        failures = []
        strategies = media_strategies(ctx.platform, options)
        st.info("会按顺序试：" + " → ".join(
            s.label + ("" if s.enabled(options)[0] else "（没开）") for s in strategies))
        for strategy in strategies:
            ok, why = strategy.enabled(options)
            if not ok:
                st.record_skipped_attempt(strategy, why)
                continue
            with st.attempt(strategy) as at:
                strategy.run(ctx, at)
            if at.ok and ctx.media_path:
                ctx.media_label = strategy.label
                st.summary(f"用「{strategy.label}」拿到了：{ctx.media_how}")
                return
            failures.append(f"{strategy.label}：{at.rec.get('message')}")
            if at.final:  # 内容本身拿不到（已删除 / 图文 / 私密），换办法也没用
                raise StepFailed(at.rec.get("message") or "内容拿不到", {"failures": failures})
            time.sleep(1.0)  # 换下一种办法前稍等一下，对平台客气点
        hint = ""
        if not options.allow_thirdparty and any(s.option_flag for s in strategies):
            hint = "（还可以在选项里打开「允许用第三方解析网站」再试一次）"
        raise StepFailed("所有办法都没拿到视频" + hint, {"failures": failures})


def _extract_audio(ctx: Ctx) -> Path:
    job = ctx.job
    with job.step("audio") as st:
        wav = ctx.workdir / "audio_16k.wav"
        try:
            extract_audio(ctx.media_path, wav, job)
        except StrategyFailed as exc:
            raise StepFailed(exc.reason, exc.detail) from None
        info = probe(wav)
        if info.get("duration"):
            ctx.info["duration"] = ctx.info.get("duration") or info["duration"]
            job.update(duration=ctx.info["duration"])
        st.summary(f"音频 {human_duration(info.get('duration'))}，16kHz 单声道")
        return wav


def _transcribe(ctx: Ctx, wav: Path) -> None:
    job, options = ctx.job, ctx.options
    with job.step("transcribe") as st:
        duration = float(ctx.info.get("duration") or probe(wav).get("duration") or 0)
        est = max(10, int(duration / 10)) if duration else None
        st.info(f"模型：{config.WHISPER_MODEL}（本机运行）" + (f"；音频 {human_duration(duration)}，预计 {est} 秒左右" if est else ""))
        out_json = ctx.workdir / "whisper_raw.json"
        state = {"last": 0.0, "lang": None, "count": 0}

        def on_line(line: str) -> None:
            if line.startswith("STATUS "):
                st.info(line[7:])
            elif line.startswith("LANG "):
                state["lang"] = line[5:].strip()
                st.info(f"识别出的语言：{lang_name(state['lang'])}")
            elif line.startswith("DONE "):
                pass
            elif line.startswith("SEG "):
                parts = line.split(" ", 3)
                if len(parts) < 4:
                    return
                start, end, text = float(parts[1]), float(parts[2]), parts[3]
                state["count"] += 1
                job.emit("segment", text, level="info", step="transcribe", start=start, end=end)
                if duration and time.time() - state["last"] > 4:
                    state["last"] = time.time()
                    pct = min(100, int(end * 100 / duration))
                    job.emit("progress", f"已转到 {human_duration(end)} / {human_duration(duration)}（{pct}%）",
                             step="transcribe", percent=pct)
            else:
                # 其他输出（第一次运行时的模型下载进度、报错等）：开发模式里能看到
                is_progress = "%|" in line or "it/s" in line
                if not is_progress or time.time() - state.get("last_raw", 0) > 1.5:
                    state["last_raw"] = time.time()
                    job.emit("output", line, level="debug", step="transcribe", strategy="mlx_whisper")

        # 用 --参数=值 的写法：提示词以「-」开头也不会被当成参数
        cmd = [config.PYTHON, "-m", "v2t.whisper_worker", str(wav), str(out_json),
               f"--language={options.language or 'auto'}", f"--model={config.WHISPER_MODEL}"]
        if options.initial_prompt.strip():
            cmd.append(f"--prompt={options.initial_prompt.strip()}")
        result = run(cmd, job=job, step="transcribe", strategy="mlx_whisper", timeout=max(1800, duration * 3),
                     on_line=on_line, cwd=config.ROOT, log_lines=False)
        if not result.ok or not out_json.exists():
            tail = result.tail(30)
            if any(k in tail for k in ("LocalEntryNotFoundError", "huggingface", "ConnectError",
                                        "ConnectionError", "ProxyError")):
                raise StepFailed("语音识别模型下载失败：第一次使用要从 huggingface.co 下载模型（约 1.6GB），"
                                 "请检查网络或代理后点「重试」", {"tail": tail})
            if "MemoryError" in tail or "Killed" in tail or result.returncode in (-9, 137):
                raise StepFailed("转写时内存不够或被系统停掉了（视频可能太长），关掉一些程序后重试", {"tail": tail})
            raise StepFailed("Whisper 转写失败（打开开发模式可以看到原始报错）", {"tail": tail})
        data = json.loads(out_json.read_text(encoding="utf-8"))
        segments = data["segments"]
        if not segments:
            raise StepFailed("没识别出任何说话内容（可能是纯音乐或者没有人声）")
        language = data.get("language") or state["lang"]
        if (language or "").startswith(("zh", "yue")):
            segments = to_simplified(segments)
        ctx.subtitle = None
        ctx.info["language"] = language
        ctx.info["whisper"] = {"segments": segments, "language": language, "prompt": data.get("prompt"),
                               "model": data.get("model")}
        st.summary(f"{lang_name(language)}，{len(segments)} 段，用时 {result.duration} 秒")


def _write_outputs(ctx: Ctx, elapsed: float) -> None:
    job = ctx.job
    with job.step("output") as st:
        if ctx.options.official_only and not ctx.subtitle:
            files = {}
            media_has_video = None
            if ctx.media_path and ctx.media_path.exists() and ctx.workdir in ctx.media_path.parents:
                files["media"] = str(ctx.media_path.relative_to(ctx.workdir))
                try:
                    media_has_video = probe(ctx.media_path)["has_video"]
                except StrategyFailed:
                    pass
            how = "完整视频已下载；平台没有交回可用的原字幕，未运行转录"
            job.update(result={"source": "platform_assets", "how": how, "method_short": "平台视频，无原字幕",
                               "language": None, "language_label": "—", "segments": 0, "chars": 0,
                               "files": files, "media_strategy": ctx.media_strategy,
                               "media_has_video": media_has_video,
                               "platform_summary": ctx.info.get("platform_summary")})
            st.summary(how)
            return
        if ctx.subtitle:
            segments = ctx.subtitle["segments"]
            language = ctx.subtitle["language"]
            how = f"文字来自{ctx.subtitle['how']}" + (f"；另已下载视频：{ctx.media_how}（无需转写）" if ctx.media_path else "（没有下载视频，也没有转写）")
            short = f"{ctx.subtitle['how']}"
            source = "subtitles"
        else:
            w = ctx.info["whisper"]
            segments, language = w["segments"], w["language"]
            model = config.WHISPER_MODEL.split("/")[-1]
            if job.data.get("source_job_id"):
                how = f"复用已下载的视频 → 本机 Whisper 转录（{model}），没有重新下载"
            elif job.data["input_type"] == "file":
                how = f"本机文件「{Path(job.data['input']).name}」→ 本机 Whisper 转写（{model}）"
            else:
                how = f"视频通过「{ctx.media_how}」拿到 → 本机 Whisper 转写（{model}）"
            short = f"{ctx.media_label or '本地文件'} → Whisper 转写"
            source = "whisper"
        meta = {
            "title": ctx.info.get("title"), "url": ctx.url, "input": job.data["input"],
            "platform_label": job.data.get("platform_label"), "duration": ctx.info.get("duration"),
            "language": language, "how": how, "source": source, "generated_at": now_iso(),
            "platform_summary": ctx.info.get("platform_summary"),
        }
        files = write_all(ctx.workdir, segments, meta)
        if ctx.subtitle and ctx.subtitle.get("raw_file"):
            files["platform_subtitle"] = ctx.subtitle["raw_file"]
        caption_tracks = []
        for track in ctx.subtitle_tracks:
            lang = track["language"]
            safe_lang = re.sub(r"[^A-Za-z0-9_-]", "_", str(lang))[:30] or "und"
            folder = ctx.workdir / "captions" / safe_lang
            folder.mkdir(parents=True, exist_ok=True)
            track_files = write_all(folder, track["segments"],
                                    {**meta, "language": lang, "how": track["how"]})
            caption_tracks.append({"language": lang, "kind": track["kind"],
                                   "raw_file": track.get("raw_file"),
                                   "files": {key: str((folder / name).relative_to(ctx.workdir))
                                             for key, name in track_files.items()}})
        extra = {}
        media_has_video = None
        if ctx.media_path and ctx.media_path.exists() and ctx.workdir in ctx.media_path.parents:
            extra["media"] = str(ctx.media_path.relative_to(ctx.workdir))  # 网页上点时间戳可以直接播放
            try:
                media_has_video = probe(ctx.media_path)["has_video"]
            except StrategyFailed:
                pass
        chars = sum(len(s["text"]) for s in segments)
        job.update(result={
            "source": source, "how": how, "method_short": short, "language": language,
            "language_label": lang_name(language), "segments": len(segments), "chars": chars,
            "files": files | extra, "media_strategy": ctx.media_strategy,
            "media_has_video": media_has_video,
            "platform_summary": ctx.info.get("platform_summary"),
            "caption_tracks": caption_tracks,
        })
        st.summary(f"写好了 md / srt / txt / json：{len(segments)} 段，{chars} 字" +
                   ("；另保存了平台原字幕" if files.get("platform_subtitle") else ""))


def _cleanup(ctx: Ctx) -> None:
    """转写完就删掉中间产物 16kHz wav（可以随时从原文件重新生成），原始下载文件保留下来方便回放。"""
    wav = ctx.workdir / "audio_16k.wav"
    if wav.exists() and ctx.job.data.get("status") == "done":
        wav.unlink()
        ctx.job.emit("log", "删掉中间文件 audio_16k.wav（原始音视频还在）", level="debug")
