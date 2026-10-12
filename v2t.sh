#!/bin/sh
# 用项目自己的 Python 环境运行 v2t：./v2t run <链接>、./v2t serve、./v2t doctor
cd "$(dirname "$0")" && exec ./.venv/bin/python -m v2t "$@"
