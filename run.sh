#!/usr/bin/env bash
# 启动 Skill 优化平台（默认 http://127.0.0.1:8765）
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "未找到 .venv，请先运行: bash scripts/setup.sh" >&2
  exit 1
fi
exec .venv/bin/python -m app.server
