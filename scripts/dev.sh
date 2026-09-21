#!/usr/bin/env bash
# TrendRadar 本机(WSL)开发服务一键启停 —— 独立于 docker 部署脚本 restart.sh。
# 用法: scripts/dev.sh {start|stop|restart|status|logs [N]}
set -euo pipefail
cd "$(dirname "$0")/.."

PID_FILE=".run/trendradar-8000.pid"
LOG_FILE=".run/trendradar-8000.log"
PORT=8000
APP=".venv/bin/python3 -m uvicorn trendradar.interfaces.api.app:app --host 0.0.0.0 --port 8000"

port_pid() { ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1; }

is_running() { [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

case "${1:-status}" in
  start)
    if is_running; then echo "已在运行 (PID $(cat "$PID_FILE"))"; exit 0; fi
    if port_pid="$(port_pid)" && [ -n "$port_pid" ]; then
      echo "端口 $PORT 已被 PID $port_pid 占用（非本脚本管理的实例，可能是会话托管进程）。"
      echo "请先让托管方停止，或: kill $port_pid 后重试。"
      exit 1
    fi
    mkdir -p .run
    nohup $APP >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    for _ in $(seq 1 30); do
      if curl -sf "http://127.0.0.1:$PORT/api/strategies" >/dev/null 2>&1; then
        echo "已启动 (PID $(cat "$PID_FILE"))，日志: $LOG_FILE"; exit 0
      fi
      sleep 0.5
    done
    echo "启动超时，查看日志: tail -50 $LOG_FILE"; exit 1
    ;;
  stop)
    if ! is_running; then
      rm -f "$PID_FILE"
      occupied="$(port_pid || true)"
      if [ -n "$occupied" ]; then
        echo "本脚本未管理服务，但端口 $PORT 被 PID $occupied 占用（外部实例）。kill $occupied 后重试，或让我停托管进程。"
      else
        echo "未在运行"
      fi
      exit 0
    fi
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    for _ in $(seq 1 20); do is_running || break; sleep 0.5; done
    is_running && kill -9 "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "已停止"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    if is_running; then
      echo "运行中 (PID $(cat "$PID_FILE"), 日志 $LOG_FILE)"
    elif p="$(port_pid)" && [ -n "$p" ]; then
      echo "端口 $PORT 有服务在跑（PID $p，非本脚本管理——可能是会话托管实例或手动启动）"
    else
      echo "未运行"
    fi
    ;;
  logs)
    tail -n "${2:-50}" "$LOG_FILE" 2>/dev/null || { echo "暂无日志"; exit 0; }
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|logs [N]}"; exit 1
    ;;
esac
