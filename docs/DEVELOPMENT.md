# Developer notes

[中文使用说明](../README.md) · [English guide](../README.en.md)

The app is written in Python 3.12, with plain JavaScript, HTML, and CSS for the local web interface. The supported development environment is macOS on Apple Silicon.

## Dependencies

Runtime dependencies are in [requirements.txt](../requirements.txt). Versions are not fully pinned; a fresh install is not a reproducible lockfile build.

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

The older Douyin cookie module remains for CLI compatibility; the web interface does not enable it. See [legacy module notes](../v2t/douyin_cookie/README.md).

X uses public URLs without Chrome cookies. Its canonical URL keeps `/video/N` so a post with several videos retains the user's choice.

The server binds to `127.0.0.1`. Mobile/LAN access and Android installation are not implemented.

## CLI and tests

```bash
./v2t.sh run --keep-video "VIDEO_URL"
./v2t.sh run --official-only "VIDEO_URL"
./v2t.sh run --no-subs --lang zh --prompt "names,terms" "VIDEO_URL"
./v2t.sh plan "VIDEO_URL"
./v2t.sh doctor
./v2t.sh jobs
```

The Douyin Chrome connector requires the running web page; it has no corresponding CLI option.

Install pytest for Python tests. JavaScript tests need Node.js (previously verified with Node.js 22).

```bash
uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest -q
node --test tests/chrome_connector.test.cjs
node --check web/app.js
```

Latest verification: 91 Python tests and 3 connector tests passed. Offline tests do not prove that a platform still provides captions. Real sample results and limitations are recorded in [CHANGELOG.md](../CHANGELOG.md).

## Versions and updates

The current branch is `codex/official-source-files`. The latest release tag remains `v0.2.0-rc.2`; the branch includes later fixes and X support. Python reports `0.2.0rc2`; the unchanged Chrome extension reports `0.2.0-rc.1`. Do not move existing release tags.

Restart the Python server after code changes. Reload the unpacked Chrome extension only when its code changes. The connector currently targets port 8765.
