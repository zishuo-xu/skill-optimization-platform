#!/usr/bin/env bash
# Skill 优化平台 · 一键启动
#
#   ./start.sh                  # 环境准备 + 停止旧服务 + 首次初始化 + 启动并打开浏览器
#   ./start.sh --reset          # 清空本地演示数据后重新初始化（再次执行基线 + 单轮优化）
#   ./start.sh --no-open        # 启动服务但不自动打开浏览器
#   ./start.sh --seed-only      # 只初始化/重置示例数据，不启动服务
#   ./start.sh --no-kill        # 不停止已在运行的服务（已在运行时只打开浏览器）
#   ./start.sh --port 8766      # 指定端口（同 SKILLOPT_PORT）
#   ./start.sh --help
#
# macOS 用户也可以直接双击 start.command。

set -euo pipefail
export PYTHONUNBUFFERED=1

cd "$(dirname "$0")"
ROOT="$(pwd)"

PYTHON_BIN="${PYTHON_BIN:-}"
OPEN_BROWSER=1
DO_RESET=0
SEED_ONLY=0
NO_KILL=0
HOST="${SKILLOPT_HOST:-127.0.0.1}"
PORT="${SKILLOPT_PORT:-8765}"

usage() {
  awk 'NR > 1 && /^set -euo pipefail/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --reset) DO_RESET=1 ;;
    --no-open) OPEN_BROWSER=0 ;;
    --seed-only) SEED_ONLY=1 ;;
    --no-kill) NO_KILL=1 ;;
    --port)
      shift
      [ $# -gt 0 ] || { echo "缺少 --port 参数" >&2; exit 2; }
      PORT="$1"
      ;;
    --host)
      shift
      [ $# -gt 0 ] || { echo "缺少 --host 参数" >&2; exit 2; }
      HOST="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1（使用 --help 查看用法）" >&2
      exit 2
      ;;
  esac
  shift
done

export SKILLOPT_HOST="$HOST"
export SKILLOPT_PORT="$PORT"

# 通过 Host:Port 访问时使用的浏览器地址（0.0.0.0 监听时改用 127.0.0.1）
BROWSER_HOST="$HOST"
if [ "$HOST" = "0.0.0.0" ] || [ "$HOST" = "::" ]; then
  BROWSER_HOST="127.0.0.1"
fi
BASE_URL="http://${BROWSER_HOST}:${PORT}"

log()  { printf '\033[36m[start]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[start]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[start]\033[0m %s\n' "$*" >&2; exit 1; }

# 1. 找到可用的 Python（3.11+）
check_python() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && check_python "$candidate"; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi
[ -n "$PYTHON_BIN" ] || fail "未找到 Python 3.11+。请安装后重试（macOS: brew install python@3.12）。"
log "使用 Python: $PYTHON_BIN ($("$PYTHON_BIN" -c 'import platform; print(platform.python_version())'))"

# 2. 创建虚拟环境
if [ ! -x .venv/bin/python ]; then
  log "创建虚拟环境 .venv ..."
  "$PYTHON_BIN" -m venv .venv || fail "创建 .venv 失败"
fi
VENV_PY="$ROOT/.venv/bin/python"

# 3. 安装/修复依赖（纯 Python wheel，无需编译）
if ! "$VENV_PY" -c 'import starlette, uvicorn, httpx, websockets, multipart' >/dev/null 2>&1; then
  log "安装依赖（首次启动需要联网，约 10-30 秒）..."
  "$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
  "$VENV_PY" -m pip install -r requirements.txt || fail "依赖安装失败，请检查网络或手动执行：.venv/bin/python -m pip install -r requirements.txt"
else
  log "依赖已就绪"
fi

# 4. 检查是否已有服务在运行
is_up() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "$BASE_URL/api/v3/healthz" >/dev/null 2>&1
  else
    "$VENV_PY" - "$BASE_URL/api/v3/healthz" >/dev/null 2>&1 <<'PY'
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=2).read()
PY
  fi
}

pgrep_lines() {
  if [ "$(uname -s)" = "Darwin" ]; then
    pgrep -fl "app.server" 2>/dev/null || true
  else
    pgrep -af "app.server" 2>/dev/null || true
  fi
}

process_is_ours() {
  local pid="$1" line cmd cwd
  [ -n "$pid" ] || return 1
  line="$(pgrep_lines | awk -v p="$pid" '$1 == p { print; exit }')"
  [ -n "$line" ] || return 1
  cmd="${line#* }"
  case "$cmd" in
    *"app.server"*) ;;
    *) return 1 ;;
  esac
  case "$cmd" in
    *"$ROOT"*) return 0 ;;
  esac
  cwd=""
  if [ -e "/proc/$pid/cwd" ]; then
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  fi
  if [ -z "$cwd" ] && command -v lsof >/dev/null 2>&1; then
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  fi
  [ -n "$cwd" ] && [ "$cwd" = "$ROOT" ]
}

kill_existing() {
  [ "$NO_KILL" -eq 1 ] && return 0
  local pids="" pid stale alive line
  add_pid() {
    case " $pids " in
      *" $1 "*) ;;
      *) pids="${pids:+$pids }$1" ;;
    esac
  }
  if [ -f "$ROOT/.run/server.pid" ]; then
    stale="$(cat "$ROOT/.run/server.pid" 2>/dev/null || true)"
    rm -f "$ROOT/.run/server.pid"
    if process_is_ours "$stale"; then add_pid "$stale"; fi
  fi
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r line; do
      pid="${line%% *}"
      [ -n "$pid" ] || continue
      [ "$pid" = "$$" ] && continue
      case "${line#* }" in
        *"app.server"*) ;;
        *) continue ;;
      esac
      if process_is_ours "$pid"; then add_pid "$pid"; fi
    done < <(pgrep_lines)
  fi
  if [ -z "$pids" ]; then
    if is_up; then
      fail "端口 ${PORT} 已被非本平台进程占用：${BASE_URL}。为避免误杀未自动终止；请用 --port 换端口或手动处理。"
    fi
    return 0
  fi
  log "停止旧服务进程：$pids"
  for pid in $pids; do kill -TERM "$pid" 2>/dev/null || true; done
  for _ in $(seq 1 20); do
    alive=0
    for pid in $pids; do
      if kill -0 "$pid" 2>/dev/null; then alive=1; fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.3
  done
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid" 2>/dev/null || true; fi
  done
  for _ in $(seq 1 10); do
    is_up || break
    sleep 0.3
  done
  log "旧服务已停止"
}

open_browser() {
  [ "$OPEN_BROWSER" -eq 1 ] || return 0
  if command -v open >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ]; then
    open "$BASE_URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$BASE_URL" >/dev/null 2>&1 || true
  elif command -v wslview >/dev/null 2>&1; then
    wslview "$BASE_URL" >/dev/null 2>&1 || true
  fi
}

if [ "$NO_KILL" -eq 1 ]; then
  if is_up; then
    if [ "$DO_RESET" -eq 1 ] || [ "$SEED_ONLY" -eq 1 ]; then
      fail "服务已在运行：${BASE_URL}。--reset/--seed-only 需要先停止服务；去掉 --no-kill 会自动停止旧服务。"
    fi
    log "服务已在运行（--no-kill）：$BASE_URL"
    open_browser
    exit 0
  fi
else
  kill_existing
  if is_up; then
    fail "旧服务未能停止：${BASE_URL}。请手动检查端口占用后重试。"
  fi
fi

# 5. 首次启动或显式 --reset 时初始化演示数据
needs_seed=0
if [ "$DO_RESET" -eq 1 ]; then
  needs_seed=1
elif [ ! -f "$ROOT/data/app.db" ]; then
  needs_seed=1
else
  project_count="$("$VENV_PY" - 2>/dev/null <<'PY' || echo 0
from app import db, persistence
db.init_db()
print(len(persistence.list_projects()))
PY
)"
  if [ "${project_count:-0}" = "0" ]; then
    needs_seed=1
  fi
fi

if [ "$needs_seed" -eq 1 ]; then
  if [ "$DO_RESET" -eq 1 ]; then
    log "重置并初始化示例项目（会删除本地 data/）..."
    "$VENV_PY" -m app.seed --reset --with-optimize
  else
    log "初始化示例项目（导入 Skill 包、发布环境与数据集、跑基线 + 单轮优化）..."
    "$VENV_PY" -m app.seed --with-optimize
  fi
else
  log "检测到已有本地数据，跳过初始化"
fi

if [ "$SEED_ONLY" -eq 1 ]; then
  log "示例数据已就绪（--seed-only：不启动服务）"
  exit 0
fi

# 6. 启动服务（Ctrl+C 同时停止服务）
SERVER_PID=""
CLEANED=0
cleanup() {
  [ "$CLEANED" -eq 1 ] && return 0
  CLEANED=1
  trap - INT TERM HUP EXIT
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$ROOT/.run/server.pid"
}
trap cleanup EXIT
on_signal() {
  cleanup
  exit 0
}
trap on_signal INT TERM HUP

log "启动服务：${BASE_URL}（按 Ctrl+C 停止）"
log "启动目录：${ROOT}；实际数据、日志文件位置及初始化进度见下方"
"$VENV_PY" -u -m app.server &
SERVER_PID=$!
mkdir -p "$ROOT/.run"
printf '%s\n' "$SERVER_PID" > "$ROOT/.run/server.pid"

# 7. 等待健康检查通过，再打开浏览器并打印演示入口
ready=0
for _ in $(seq 1 40); do
  if is_up; then ready=1; break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "服务启动失败（进程已退出），请查看上方日志。常见原因：端口 $PORT 被占用（可用 --port 8766 重试）"
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  warn "健康检查超时，请手动打开 $BASE_URL 查看；服务仍在运行。"
fi

project_links="$("$VENV_PY" - "$BASE_URL" 2>/dev/null <<'PY' || true
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/v3/projects", timeout=2) as resp:
        items = json.load(resp).get("items") or []
    for item in items:
        print(item.get("name", "示例项目") + "|" + sys.argv[1] + "/#/projects/" + item["id"] + "/overview")
except Exception:
    pass
PY
)"

printf '\n'
if [ "$ready" -eq 1 ]; then
  log "服务已就绪（健康检查通过）：$BASE_URL"
else
  warn "进程仍在运行，但健康检查尚未通过：$BASE_URL"
fi
if [ -n "$project_links" ]; then
  while IFS='|' read -r name link; do
    [ -n "$name" ] && log "示例项目（${name}）：${link}"
  done <<< "$project_links"
fi
log "按 Ctrl+C 停止服务（关闭此终端会同时停止服务）"
printf '\n'

open_browser
wait "$SERVER_PID"
