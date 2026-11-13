#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
V2T_AAR=app/libs/sherpa-onnx-2.0.1.aar
mkdir -p app/libs
if [[ ! -f "$V2T_AAR" ]]; then
    curl -fL --retry 3 https://github.com/k2-fsa/sherpa-onnx/releases/download/v2.0.1/sherpa-onnx-2.0.1.aar -o "$V2T_AAR.part"
    mv "$V2T_AAR.part" "$V2T_AAR"
fi
V2T_HASH=$(shasum -a 256 "$V2T_AAR" | cut -d ' ' -f 1)
[[ "$V2T_HASH" == a8d09d1d32f3eb8520482c6d3d43fae674681c5b050385967c70a3c2c9bf0ee7 ]] || { echo "sherpa-onnx 文件校验不符"; exit 1; }
V2T_YTDLP=app/src/main/res/raw/ytdlp
mkdir -p app/src/main/res/raw
if [[ ! -f "$V2T_YTDLP" ]]; then
    curl -fL --retry 3 https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp -o "$V2T_YTDLP.part"
    mv "$V2T_YTDLP.part" "$V2T_YTDLP"
fi
V2T_YTDLP_HASH=$(shasum -a 256 "$V2T_YTDLP" | cut -d ' ' -f 1)
[[ "$V2T_YTDLP_HASH" == e51e69e89489f199530f13d32a4342da3ddb2cf34842f364603a066db05afbf3 ]] || { echo "yt-dlp 文件校验不符"; exit 1; }
