# v2t · Video Downloader and Transcripts

[中文](README.md) | **English**

Paste a video link to download the video and its captions. If a video has no captions, v2t turns the speech into text on your own computer. Video and audio files on your computer work too.

Supported: Douyin, Xiaohongshu, YouTube, Bilibili, X (Twitter). Other sites may work, but there is no guarantee.

## What you need

- A Mac with an **Apple M-series chip** (M1 to M5). Speech-to-text only works on these Macs.
- Other computers (Intel Mac, Windows, Linux) can try downloading videos and platform captions. This is not fully tested, and the app cannot transcribe there. See the [developer notes](docs/DEVELOPMENT.md#without-apple-silicon-or-mlx).
- There is a separate Android preview app; see the [Android guide](android/README.md) (in Chinese). There is no iPhone app.
- The app's buttons are in Chinese.

## Install (once)

Open Terminal on your Mac and run the three blocks below in order. Install [Homebrew](https://brew.sh/) first.

**1. Get the tools and the project**

```bash
brew install uv ffmpeg deno
git clone https://github.com/feihaitun/v2t.git
cd v2t
```

**2. Set up the environment** (the first run downloads quite a lot; let it finish)

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium
```

> Only want downloads, no speech-to-text (or not on an M-series Mac)? Replace `requirements.txt` with `requirements-base.txt` in the third line.

**3. Check, then open**

```bash
./v2t.sh doctor
./v2t.sh serve
```

Your browser opens [127.0.0.1:8765](http://127.0.0.1:8765/). That is the app. Keep the Terminal window open while you use it; press `Ctrl+C` to quit.

Next time, just double-click **「打开视频转文字.command」** in the project folder.

## How to use

1. Paste a video link into the box. A whole share message works too. You can also drag in a video or audio file.
2. Under 「你要什么？」 (What do you want?), pick one:
   - **只下载视频** (Video only): save the video.
   - **视频＋字幕** (Video + captions): save the video and get captions.
   - **只要字幕** (Captions only): text only.
3. When it finishes, click 「下载视频」 (Download video), 「下载 srt」 (Download srt), etc. to save the files.

Platform captions are used first. Speech-to-text only runs when there are none. The first transcription downloads a speech model, so it takes longer.

## Videos that need a login (optional)

v2t works without any account. Some videos or captions are only available when logged in. For those, v2t can borrow the login from Chrome on your computer:

- **YouTube, Bilibili** (Mac only): just be logged in to Chrome. When getting captions or downloading a video, v2t tries the Chrome login automatically and falls back to logged-out mode if that fails. macOS may ask whether to allow access to "Chrome Safe Storage"; click Allow.
- **Douyin**: install the included Chrome extension first:
  1. Go to `chrome://extensions` in Chrome and turn on Developer mode (top right).
  2. Click "Load unpacked" and select the project's `chrome-connector` folder.
  3. Open the app page in the same Chrome, turn on 「使用已登录的 Chrome 获取抖音视频和字幕」 (Use logged-in Chrome for Douyin), and wait for 「Chrome 已连接」 (Chrome connected).
- **X**: public posts only.

Login data (cookies) is used only in memory on your computer, briefly. It is never saved to a file or uploaded. A login only gets what your account can already see.

## FAQ

- **Results gone after refreshing the page?** The page keeps only the current item. Refreshing, closing the page, or starting the next item clears temporary files. Download your results first.
- **Typos in the transcript?** Speech-to-text makes mistakes. The page labels platform captions and v2t's own transcripts separately. Text burned into the video image is not recognized.
- **Download failed?** Run `./v2t.sh doctor` first. Platforms change often, and updating the downloader usually helps: `uv pip install --python .venv/bin/python -U "yt-dlp[default,curl-cffi]"`.
- **Updated the code?** Press `Ctrl+C` to stop the old server, then run `./v2t.sh serve` again. Refreshing the page is not enough.

## Disclaimer

- Only download and use content you have the right to use. Follow copyright law and each platform's terms.
- This project is not affiliated with any of the platforms above.
- Provided as is, with no guarantee that it keeps working. The author is not responsible for how you use it.

## License

[GPL-3.0](LICENSE). Free to use, modify, and share, including commercially. If you distribute a modified version, you must release its full source code under GPL-3.0 as well.

Code structure and libraries: [developer notes](docs/DEVELOPMENT.md).
