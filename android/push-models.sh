#!/bin/zsh
set -euo pipefail
V2T_MODEL_ROOT="${1:-${0:A:h}/models}"
V2T_DEST=/sdcard/Android/data/io.github.feihaitun.v2t/files
V2T_STREAMING=sherpa-onnx-x-asr-960ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05
V2T_OFFLINE=sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03
python3 - "$V2T_MODEL_ROOT" "${0:A:h}/app/src/main/assets/model-checksums.json" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
for name,expected in json.load(open(sys.argv[2])).items():
    with (root/name).open('rb') as source:
        actual=hashlib.file_digest(source,'sha256').hexdigest()
    if actual != expected: raise SystemExit(f'模型校验不符：{name}')
PY
adb get-state >/dev/null
for model in "$V2T_STREAMING" "$V2T_OFFLINE"; do
    [[ -f "$V2T_MODEL_ROOT/$model/tokens.txt" ]] || { echo "缺少模型：$model"; exit 1; }
    adb shell mkdir -p "$V2T_DEST/models/$model"
    for asset in "$V2T_MODEL_ROOT/$model"/*.onnx "$V2T_MODEL_ROOT/$model/tokens.txt"; do
        adb push "$asset" "$V2T_DEST/models/$model/" >/dev/null
    done
done
if [[ -f "$V2T_MODEL_ROOT/$V2T_OFFLINE/test_wavs/0.wav" ]]; then
    adb push "$V2T_MODEL_ROOT/$V2T_OFFLINE/test_wavs/0.wav" "$V2T_DEST/test.wav" >/dev/null
fi
adb shell chmod -R a+rX "$V2T_DEST/models"
echo "中英识别模型已放入 V2T。"
