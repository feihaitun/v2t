# v2t · 视频下载与文字稿

**中文** | [English](README.en.md)

粘贴视频链接，下载视频和字幕。没有字幕时，把视频里的声音转成文字。也能处理电脑上的视频和音频。

## 能做什么

- 支持抖音、小红书、YouTube、B站、X / Twitter；其他网站可以试，但不保证成功。
- 优先拿平台已有字幕。YouTube 和 B站有中英文字幕时，会分别保存。
- 可以使用你在 Chrome 里的登录状态，获取账号能访问的视频和字幕。
- 没有字幕时，用 Whisper 在自己的电脑上识别声音。可下载 TXT、SRT、Markdown 和 JSON，也能回看历史任务。

完整功能目前面向 **Apple M 系列 Mac，不限于 M5**：M1、M2、M3、M4、M5 都属于这一系列，仍需满足所装依赖的系统和内存要求。项目使用 Whisper 的 MLX 实现来加速语音识别，并非每一款芯片都已实测。

| 功能 | 需要语音识别组件吗？ |
|---|---|
| 下载视频或音频 | 不需要 |
| 下载平台已有字幕、导出文字稿 | 不需要 |
| 没有字幕时，从声音生成文字 | 需要；当前项目只接入了 MLX Whisper |

**没装语音识别组件，仍可启动网页和下载。**如果在普通模式遇到无字幕视频，转写步骤会失败；请选「只下载平台字幕源文件和完整视频，不转录」。环境自检可能提示语音识别缺失，这不阻止启动网页。

Whisper 本身支持 CPU；本项目尚未自动接入 CPU 版本。Intel Mac、Windows、Linux 可以尝试基础下载，但完整流程未验证，YouTube/B站的 Chrome 登录读取目前只适配 macOS。没有 Android 或 iPhone App。网页按钮目前是中文。

## 安装一次

打开 Mac 的「终端」，按顺序复制运行。需要先装好 [Homebrew](https://brew.sh/)。这个仓库目前是私有的，需要仓库访问权限才能下载。

**1. 下载工具和项目。** `uv` 管理 Python，ffmpeg 处理声音和画面，Deno 帮助下载 YouTube。

```bash
brew install uv ffmpeg deno
git clone https://github.com/feihaitun/v2t.git
cd v2t
```

已有项目文件夹就直接进入它，不用再克隆。

**2. 安装 Python 3.12 和所需的库。** 第一次会下载一些文件，请等它完成。

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium
```

只想下载、不需要语音识别：把上面安装命令中的 `requirements.txt` 换成 `requirements-base.txt`，其余步骤相同。Apple M 系列 Mac 以后想加转写功能，再安装完整依赖即可。其他电脑的安装入口和 CPU 转写办法见[兼容性说明](docs/DEVELOPMENT.md#without-apple-silicon-or-mlx)。

**3. 检查并启动。**

```bash
./v2t.sh doctor
./v2t.sh serve
```

在这台 Mac 的浏览器打开 [127.0.0.1:8765](http://127.0.0.1:8765/)。保持终端窗口打开，按 `Ctrl+C` 停止。第一次识别声音时还会下载模型。

以后双击项目里的 **「打开视频转文字.command」** 就能启动。

## 怎么用

1. 粘贴视频链接，或者把本地视频、音频拖进输入框。
2. 按需要选择：
   - **只要文字**：直接点「开始转文字」。
   - **视频和文字都要**：在「选项」勾选「下载完整视频」，再开始。
   - **只拿平台文件，不识别声音**：勾选「只下载平台字幕源文件和完整视频，不转录」。支持抖音、B站、YouTube 和 X。
3. 完成后点「下载视频」「下载 txt」或「下载 srt」。也可以点「打开文件夹」。

只拿平台文件时，如果没有字幕，可以再点 **「用已下载视频转录」**，不用重新下载。默认只要文字时，可能只下载音频，即使文件后缀是 `.mp4` 也不一定有画面。

## 使用已登录的 Chrome（可选）

**YouTube / B站：**先在 Chrome 登录，再选择「只下载平台字幕源文件和完整视频，不转录」。工具会尝试使用该网站的登录 Cookie（登录凭据），只在本机内存里使用，不保存 Cookie 文件。

**抖音：**先在日常 Chrome 登录，然后设置一次：

1. 打开 `chrome://extensions`，开启「开发者模式」。
2. 点「加载已解压的扩展程序」，选择项目里的 `chrome-connector` 文件夹。
3. 在同一个 Chrome 打开本机工具网页，开启「使用已登录的 Chrome 获取抖音视频和字幕」，看到「Chrome 已连接」后使用。保持 Chrome 和工具网页打开。

抖音扩展让已登录的网页提供视频资料，Cookie 留在 Chrome。**X 目前只用公开链接**，不用连接器。登录不保证每个视频都有可下载字幕，也不会绕过账号权限或平台验证。

## 需要知道

- 平台字幕和自己识别的文字会标明来源；识别结果可能有错字。画面上印着的字暂时不识别。
- 视频清晰度和字幕语言取决于平台提供的内容，不保证是上传前的原文件。
- 文件存在项目的 `data/jobs/`。换电脑要单独备份这里，GitHub 不保存你的任务和登录状态。
- 更新项目后，停止旧服务，再运行 `./v2t.sh serve`；只刷新网页不够。

[给开发者：用到的库、代码结构、测试](docs/DEVELOPMENT.md) · [更新记录](CHANGELOG.md)
