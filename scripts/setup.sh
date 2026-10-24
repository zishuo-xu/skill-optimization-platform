#!/usr/bin/env bash
# 创建本地虚拟环境并安装依赖。用法: bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "找不到 python3，请先安装 Python 3.11+" >&2
  exit 1
fi
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11), "需要 Python 3.11+"'

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt
echo "依赖安装完成。下一步: .venv/bin/python -m app.seed"
