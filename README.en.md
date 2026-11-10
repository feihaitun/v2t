# v2t · Video downloads and transcripts

[中文](README.md) | **English**

Paste a video link to download the video and captions. If there are no captions, turn the speech into text on your computer. Local video and audio files work too.

## What it does

- Works with Douyin, Xiaohongshu, YouTube, Bilibili, and X / Twitter. Other sites may work.
- Uses platform captions first. Saves Chinese and English captions separately when YouTube or Bilibili provides them.
- Can use your existing Chrome login to get videos and captions your account can access.
- Uses Whisper on your computer when captions are missing. Export TXT, SRT, Markdown, or JSON, and revisit past tasks.

The full workflow targets **Apple M-series Macs, not just M5**. M1, M2, M3, M4, and M5 are all in this family; installed dependencies still have OS and memory requirements. This project uses the MLX implementation of Whisper for speech recognition. Not every chip has been tested.

| Feature | Needs speech recognition installed? |
|---|---|
| Download video or audio | No |
| Download existing platform captions and export text | No |
| Create text from speech when captions are missing | Yes; the app currently uses MLX Whisper only |

**Without speech recognition, the web app and downloads can still work.** Normal mode will fail at transcription if captions are missing. Choose the platform-files-only option instead. The environment check may report missing speech recognition; that does not block the web server.

Whisper itself supports CPUs, but this app has no automatic CPU fallback yet. Intel Mac, Windows, and Linux users can try basic downloads; the full workflow is unverified, and YouTube/Bilibili Chrome login reading currently targets macOS only. There is no Android or iPhone app. The app's buttons are in Chinese.

## Install once

Open Terminal on your Mac and run these steps in order. Install [Homebrew](https://brew.sh/) first.

**1. Get the tools and project.** `uv` manages Python, ffmpeg handles video and audio, and Deno helps with YouTube downloads.

```bash
brew install uv ffmpeg deno
git clone https://github.com/feihaitun/v2t.git
cd v2t
```

If you already have the project, open its folder in Terminal instead of cloning again.

**2. Install Python 3.12 and the required libraries.** Wait for the downloads to finish.

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium
```

For downloads without speech recognition, replace `requirements.txt` with `requirements-base.txt` in the install command above. Keep the other steps. On an Apple M-series Mac, install the full dependencies later to add transcription. See [compatibility notes](docs/DEVELOPMENT.md#without-apple-silicon-or-mlx) for other computers and a separate CPU transcription option.

**3. Check and start.**

```bash
./v2t.sh doctor
./v2t.sh serve
```

Open [127.0.0.1:8765](http://127.0.0.1:8765/) in a browser on the same Mac. Keep Terminal open; press `Ctrl+C` to stop. The first speech recognition task also downloads a model.

Next time, double-click **打开视频转文字.command** in the project folder to start.

## Use it

1. Paste a video link, or drag a local video or audio file into the input box.
2. Choose what you want:
   - **Text only:** click **开始转文字** (Start transcription).
   - **Video and text:** open **选项** (Options), check **下载完整视频** (Download full video), then start.
   - **Platform files only:** check **只下载平台字幕源文件和完整视频，不转录** (Download platform captions and video, without transcription). Works with Douyin, Bilibili, YouTube, and X.
3. Click **下载视频** (Download video), **下载 txt**, or **下载 srt**. **打开文件夹** opens the output folder.

If a platform-files task has no captions, click **用已下载视频转录** (Transcribe the downloaded video). It reuses the file. Text-only mode may download audio only; an `.mp4` file can contain sound without a picture.

## Use your Chrome login (optional)

**YouTube / Bilibili:** sign in to the site in Chrome, then choose the platform-files option. The tool tries that site's login cookies (saved login information), using them only in local memory, without saving a cookie file.

**Douyin:** sign in using your usual Chrome profile, then set this up once:

1. Open `chrome://extensions` and enable **Developer mode**.
2. Click **Load unpacked** and select the project's `chrome-connector` folder.
3. Open the local tool page in that same Chrome profile. Enable **使用已登录的 Chrome 获取抖音视频和字幕** and wait for **Chrome 已连接** (Chrome connected). Keep Chrome and the tool page open.

The Douyin extension gets video details from the signed-in page; cookies stay in Chrome. **X currently uses public links only**, without the extension. Signing in does not guarantee captions or bypass account permissions or platform checks.

## Good to know

- Results say whether text came from platform captions or speech recognition. Speech recognition can make mistakes. Text printed into the video image is not read.
- Video quality and caption languages depend on what the platform provides. Downloads are not guaranteed to be the creator's original upload file.
- Files stay in `data/jobs/` inside the project. Back up that folder when moving computers; GitHub does not store your tasks or login state.
- After updating the project, stop the old service and run `./v2t.sh serve` again. Refreshing the browser is not enough.
- Only process content you have the right to download and use, and follow each platform's rules. This tool is not affiliated with those platforms.

[For developers: libraries and code structure](docs/DEVELOPMENT.md)
