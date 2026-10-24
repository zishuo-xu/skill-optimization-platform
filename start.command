#!/usr/bin/env bash
# macOS 双击启动：在终端中运行 ./start.sh（首次可能提示“无法验证开发者”，右键→打开即可）
cd "$(dirname "$0")" || exit 1
exec bash ./start.sh "$@"
