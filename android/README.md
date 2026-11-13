# V2T 安卓预览版

把本项目的视频下载和字幕功能放到手机上。源码独立放在 `android/`，不改变桌面端的入口。应用名称是 **V2T 视频下载**，包名 `io.github.feihaitun.v2t`，当前版本 `0.1.0`。

## 手机怎么用

1. 在小红书、B 站、X / Twitter 或 YouTube 中分享具体的视频链接给 V2T，也可以复制完整分享文案后粘贴。
2. 点「解析视频和字幕」，选择实际返回的清晰度和保存方式：视频、音频、视频＋字幕、只要字幕。
3. 有平台字幕时优先下载原字幕；没有字幕时可用手机上的中英模型转写。也能选择手机里的音视频文件转文字。
4. 到「文件」中打开、分享，或「保存到手机」。视频保存到 `Movies/V2T`，音频到 `Music/V2T`，文字稿到 `Download/V2T`。

转写输出 TXT、SRT 和带时间戳的 JSON。平台原字幕另存一份。X 的动态图按平台实际提供的无声 MP4 保存；不强制转成体积更大的 GIF。一个 X 帖子中的多项媒体可分别选择，首版最多读取 8 项。

本地转写保留中英文原话，不是中英互译。SRT 的时间戳按语音分段生成，不是逐字对齐。

## 登录和 Cookie / Token

- **只需保存一次**，关闭 App、强制结束进程或重启手机不会主动清除登录资料。平台主动使会话失效时才需要刷新；卸载或清除应用数据会删除本机保存的会话。
- 电脑 Chrome 已登录时，优先通过 USB 同步，手机无需重新登录。`sync-sessions.py` 只读取所选平台在 Chrome 当前配置中的 Cookie；用手机生成的公钥加密后传入 V2T 私有目录，手机解密后重新加密存储。未找到会话的平台会跳过，不覆盖手机已有资料。
- 「账号 → 打开官方网页登录」在 V2T 自己的 WebView 中打开平台网站。登录后点「保存并返回」，再重新解析视频。
- 「导入 Cookie」接受本人账号的 Cookie 请求头或 Netscape Cookie 文件内容。例如 B 站的 `SESSDATA=…`、X 的 `auth_token=…`、小红书的 `web_session=…`；YouTube 通常需要完整 Cookie。不要把真实值写进源码、问题报告或聊天记录。
- 手动导入优先于网页会话；重新网页登录并保存后恢复使用网页会话。Google 等平台可能限制内嵌网页登录，此时可用本人浏览器导出的 Cookie，公开资源则优先直接下载。
- 「已保存登录态」只表示存在对应 Cookie；是否有效由下一次平台请求判断。Cookie 失效后重新登录或导入。
- 不读取手机 Chrome、Twitter、哔哩哔哩等其他 App 的私有会话，不需要 Root、浏览器扩展或安装抓包证书。

导入的 Cookie 和网页登录后的会话快照使用 Android Keystore + AES-GCM 加密保存，网页 Cookie 同时由应用自己的 WebView 管理。交给 yt-dlp 的临时 Cookie 文件仅位于应用私有缓存，结束后删除；界面和日志不输出 Cookie 或下载器的原始错误全文。平台字幕、音视频仍会直接请求相应平台/CDN；本地语音识别不上传音视频。

USB 同步工具在 macOS 上使用：

```sh
cd android
uv venv .venv-sync
uv pip install --python .venv-sync/bin/python -r requirements-sync.txt
.venv-sync/bin/python sync-sessions.py
# 只同步指定平台或 Chrome 配置：
.venv-sync/bin/python sync-sessions.py --sites bili youtube --profile 'Profile 1'
```

默认四个平台都会尝试；只查询这些站点的 Cookie 行，不复制整个浏览器数据库、不读取其他网站、不上传到服务器。Cookie 明文不进入命令参数、磁盘文件或日志。传输使用 RSA-OAEP + AES-GCM，接收私钥保留在手机 Android Keystore 中。连接多台手机时加 `--serial`。此 USB 接收方式使用当前个人测试构建的 `run-as` 权限，正式签名版本需另做设备配对入口。

应用关闭云备份和设备迁移备份。原始分享链接会记录在本机任务历史里，以便重试；其中可能包含平台必需的分享参数。卸载会删除未导出的文件和模型。

## 构建与安装

要求 JDK 21、Android SDK Platform 37、ADB 和一台允许 USB 调试的 ARM64 安卓手机。最低 Android 11，目标 Android 16。Gradle 使用项目自带 wrapper。

```sh
cd android
./install.sh
```

`install.sh` 会验证并准备固定版本依赖，编译 APK、覆盖安装到连接的手机并打开应用。默认使用 Homebrew 安装的 JDK / SDK 路径；其他环境先设置 `JAVA_HOME`、`ANDROID_HOME`。覆盖安装同签名 APK 会保留登录资料、任务和模型。

APK：`app/build/outputs/apk/debug/app-debug.apk`。这是供个人测试的 debug 签名预览版，尚未发布应用商店。

## 本地识别模型

用 sherpa-onnx 运行两套 X-ASR 中英模型（流式模型边听边出字，句子结束后再用离线模型重识别一遍）。模型约 330 MB，不打包进 APK。

先从 sherpa-onnx 官方发布页下载并解压两个模型，再推到手机。脚本会先核对文件校验值：

```sh
mkdir -p models
for m in sherpa-onnx-x-asr-960ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05 \
         sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03; do
  curl -fL https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$m.tar.bz2 | tar xj -C models
done
./push-models.sh "$PWD/models"
```

也可以生成可在手机「设置 → 导入模型 ZIP」中选择的模型包，需 Python 3.11+：

```sh
python3 pack-models.py --models models --output /tmp/v2t-xasr-models.zip
adb push /tmp/v2t-xasr-models.zip /sdcard/Download/v2t-xasr-models.zip
```

模型名称：

- `sherpa-onnx-x-asr-960ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05`
- `sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03`

打包及手机 ZIP 导入使用 `app/src/main/assets/model-checksums.json` 中 8 个文件的 SHA-256 校验。导入先写入临时目录，完整校验成功后才替换原模型。首次安装没有模型时仍可下载媒体和平台字幕。

## 验证

```sh
./gradlew :app:testDebugUnitTest :app:lintDebug :app:assembleDebug :app:assembleDebugAndroidTest
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w \
  -e class io.github.feihaitun.v2t.RuntimeTest,io.github.feihaitun.v2t.TaskFlowTest \
  io.github.feihaitun.v2t.test/androidx.test.runner.AndroidJUnitRunner
```

设备测试需要已安装模型以及模型自带的约 10 秒 `test.wav`；`push-models.sh` 可准备该样本。测试覆盖下载运行时、FFmpeg 合并、无声音轨判断、Cookie 加密和域名隔离、临时文件清理、本地转写、前台服务到系统文件导出的完整流程、损坏模型导入保留旧模型。

`NetworkTest` 是显式运行的真实网站测试。测试链接从设备应用外部专用目录的 `network-samples.json` 读取，不嵌入 APK 或仓库；键为 `bili`、`x`、`xhs`、`youtube`。例如：

```sh
adb shell am instrument -w -e class io.github.feihaitun.v2t.NetworkTest \
  -e site xhs -e download true \
  io.github.feihaitun.v2t.test/androidx.test.runner.AndroidJUnitRunner
```

测试结果只保存媒体数量、下载大小、实际宽高、字幕条数等指标，不保存 Cookie、标题或资源 URL。具体真机验证结果见 `VALIDATION.md`。

## 首版范围

支持单条视频、YouTube Shorts 和 X 单帖多媒体，不批量下载整个账号、播放列表或正在直播的内容。每次处理一个任务；停止或进程被系统终止后可重新解析开始，暂不提供任务队列和跨进程断点续传。没有音轨的动态图会保存媒体并说明无法识别人声。

清晰度和字幕取决于平台实际资源、账号权限、地区及当时网络；不制造不存在的高清档位。网站规则变化时可以在「设置」更新 yt-dlp，小红书网页解析则需更新 App。付费、DRM 和账号无权查看的内容不在首版范围。

实测设备为 OnePlus 15 / OxygenOS 16.0.9 / 12 GB RAM，系统内存页为 4 KB。尚未完成其他厂商和 16 KB 内存页设备的兼容验证，打包的 FFmpeg 依赖仍需单独检查其 16 KB 兼容性。

## 主要组件

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)，内置 `2026.10.26` 官方 zipapp，包含 EJS。设置中的更新从官方 release 获取。
- [youtubedl-android](https://github.com/yausername/youtubedl-android) `0.18.1`，提供 Android Python、FFmpeg 和 QuickJS 运行时。QuickJS 配合 [EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS) 处理 YouTube 的解析脚本。
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) `2.0.1` 及其 X-ASR 中英模型。
- AndroidX / Jetpack Compose 原生界面、MediaStore 导出、FileProvider 分享。

本项目整体采用 GPL-3.0（见根目录 `LICENSE`），与 youtubedl-android 的 GPL-3.0 一致。FFmpeg、Python、模型及 AAR 内部依赖各自保留原许可证；分发 APK 时需一并遵守。当前 APK 是 debug 签名的测试构建。
