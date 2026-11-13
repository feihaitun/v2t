#!/usr/bin/env python3
"""Build the app's offline model ZIP from local X-ASR model files."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--models", type=Path, default=Path(__file__).parent / "models")
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
manifest = json.loads((Path(__file__).parent / "app/src/main/assets/model-checksums.json").read_text())
for name, expected in manifest.items():
    with (args.models / name).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise SystemExit(f"模型校验不符：{name}")
args.output.parent.mkdir(parents=True, exist_ok=True)
temporary = args.output.with_suffix(".zip.part")
try:
    with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=1) as archive:
        for name in manifest:
            archive.write(args.models / name, name)
    temporary.replace(args.output)
finally:
    temporary.unlink(missing_ok=True)
print(f"模型包已生成：{args.output}（{args.output.stat().st_size / 1_000_000:.1f} MB）")
