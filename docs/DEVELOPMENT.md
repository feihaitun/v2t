# Developer notes

[中文使用说明](../README.md) · [English guide](../README.en.md)

The app is written in Python 3.12, with plain JavaScript, HTML, and CSS for the local web interface. The supported development environment is macOS on Apple Silicon.

## Dependencies

Download/web dependencies are in [requirements-base.txt](../requirements-base.txt). The full [requirements.txt](../requirements.txt) adds MLX Whisper for Apple Silicon Macs. Versions are not fully pinned; a fresh install is not a reproducible lockfile build.

| Component | Purpose |
|---|---|
| yt-dlp, curl-cffi | Platform metadata, captions, and media downloads |
| Playwright + Chromium | Browser-based download fallbacks |
| MLX Whisper | Local speech recognition on Apple Silicon |
| ffmpeg / ffprobe | Merge video and audio, extract sound, inspect actual media tracks |
| FastAPI + Uvicorn | Local API, web pages, and job queue |
| httpx | HTTP requests |
| OpenCC | Convert recognized Chinese text to simplified Chinese |
| Deno (or Node.js fallback) | JavaScript runtime used by yt-dlp for YouTube |

## Processing flow

Link or local file → identify source → try platform captions → download media if needed → extract audio and transcribe if needed → save outputs.

- Normal mode prefers existing captions, then falls back to local speech recognition.
- `keep_video` requests a full video even when captions are found.
- `official_only` supports Douyin, Bilibili, YouTube, and X. It forces a full video download and never runs Whisper. Missing captions are reported as missing.
- A completed source-files task without captions can create a separate transcription task using its existing media file.
- Media tracks are inspected with ffprobe; an MP4 extension alone does not prove that a file contains video.

## Code map

```text
v2t/
  pipeline.py          Processing steps and strategy selection
  platforms/           Per-platform link, caption, and download handling
  ytdlp.py             yt-dlp calls and error messages
  media.py             ffmpeg and ffprobe
  whisper_worker.py    Speech recognition in a separate process
  outputs.py           TXT, SRT, Markdown, and JSON
  server.py            Local web API and job queue
  jobs.py              Job state and event logs
  chrome_bridge.py     Douyin connector requests and responses
  douyin_cookie/       Site-scoped cookie reading; legacy Douyin path
  cli.py               Command-line entry point
chrome-connector/      Optional desktop Chrome extension for Douyin
web/                   Local web interface
```

Each task lives in `data/jobs/<id>/`. `job.json` stores status and options; `events.jsonl` stores events. Caption source files and downloaded media stay alongside the generated transcripts. The temporary 16 kHz audio file is removed after successful transcription. Local jobs, browser profiles, uploads, and `.venv/` are ignored by Git.

## Login and browser boundaries

Bilibili and YouTube source-files strategies read only their own site's Chrome cookie rows, read-only, and pass cookies to download code in memory. Cookie values must not appear in options, CLI arguments, logs, or saved files.

The Douyin connector uses the existing Chrome session without extracting credentials. It opens its own target-video tab and closes that tab after use. Local requests are video-specific, use one-time tokens, and expire after 35 seconds. Keep Origin/Host checks and media redirect validation. See the [connector documentation](../chrome-connector/README.md).

The older Douyin cookie module remains for CLI compatibility; the web interface does not enable it. Its explicit CLI flag is `--douyin-cookie-subtitles`; `--douyin-cookie-profile` selects a Chrome profile. The shared loader also serves YouTube and Bilibili with their own domain filters.

X uses public URLs without Chrome cookies. Its canonical URL keeps `/video/N` so a post with several videos retains the user's choice.

The server binds to `127.0.0.1`. Mobile/LAN access and Android installation are not implemented.

## CLI

```bash
./v2t.sh run --keep-video "VIDEO_URL"
./v2t.sh run --official-only "VIDEO_URL"
./v2t.sh run --no-subs --lang zh --prompt "names,terms" "VIDEO_URL"
./v2t.sh plan "VIDEO_URL"
./v2t.sh doctor
./v2t.sh jobs
```

The Douyin Chrome connector requires the running web page; it has no corresponding CLI option.

## Versions and updates

The current branch is `codex/official-source-files`. The latest release tag remains `v0.2.0-rc.2`; the branch includes later fixes and X support. Python reports `0.2.0rc2`; the unchanged Chrome extension reports `0.2.0-rc.1`.

Restart the Python server after code changes. Reload the unpacked Chrome extension only when its code changes. The connector currently targets port 8765.

## Without Apple Silicon or MLX

MLX Whisper is imported in the speech worker, not at web-server startup. Downloads and existing platform captions do not need it. The doctor check reports missing MLX as a failure, but does not prevent starting the server. In normal mode, missing captions trigger transcription and that step fails if MLX is unavailable. Use `official_only` for supported platforms to avoid transcription.

For an Intel Mac, use the main guide with `requirements-base.txt`. For Linux or Windows, install Python 3.12, ffmpeg and Deno using your OS's installation method, then try the commands below from the project folder. These are experimental entry points, not a tested cross-platform release. Chrome cookie reading for YouTube/Bilibili is macOS-specific; Finder buttons and the `.command` launcher are also Mac-specific.

Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-base.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m v2t serve
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-base.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m v2t serve
```

### A separate CPU transcription option

[OpenAI Whisper supports CPU execution](https://github.com/openai/whisper/blob/main/whisper/transcribe.py). A different speech model is not inherently required: it is the execution library that changes. This project's `mlx-community/whisper-large-v3-turbo` weights cannot simply be passed to the OpenAI Whisper loader.

After downloading a video, you can use OpenAI Whisper separately with ffmpeg installed. In a separate Python environment, run:

```bash
python -m pip install openai-whisper
python -m whisper "video.mp4" --model base --device cpu --fp16 False --output_dir transcripts
```

`base` is a smaller starting model; larger models need more memory and usually take longer on a CPU. This command is an external workflow, has not been tested here, and does not connect to the app's transcription button or history.

To integrate CPU transcription, add a backend choice in `whisper_worker.py`, use compatible weights, preserve the worker's output format, and update health checks and error messages. Installing `openai-whisper` alone does not make this app switch backends.
