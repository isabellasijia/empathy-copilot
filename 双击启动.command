#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PORT=4173
APP_URL="http://127.0.0.1:${APP_PORT}/?case=S00018#assist"
HEALTH_URL="http://127.0.0.1:${APP_PORT}/api/health"

cd "$PROJECT_DIR"
clear
echo "共情双舱 · 智能客服工作台"
echo "================================"

if curl --silent --fail --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
  echo "网站已经在运行，正在打开浏览器…"
  open "$APP_URL"
  exit 0
fi

if lsof -nP -iTCP:"$APP_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  for candidate in {4174..4190}; do
    if ! lsof -nP -iTCP:"$candidate" -sTCP:LISTEN >/dev/null 2>&1; then
      APP_PORT="$candidate"
      APP_URL="http://127.0.0.1:${APP_PORT}/?case=S00018#assist"
      HEALTH_URL="http://127.0.0.1:${APP_PORT}/api/health"
      echo "4173 端口正被其他程序使用，将改用 ${APP_PORT}。"
      break
    fi
  done
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "首次启动，正在创建运行环境…"
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import fastapi, uvicorn, openai, openpyxl" >/dev/null 2>&1; then
  echo "正在安装项目依赖，请稍候…"
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "正在启动服务…"
echo "启动后会自动打开浏览器。此窗口需保持打开。"
echo "需要停止网站时，在这里按 Control + C。"

(
  for _ in {1..40}; do
    if curl --silent --fail --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
      open "$APP_URL"
      exit 0
    fi
    sleep 0.25
  done
  echo "服务启动时间较长，请稍后手动打开：$APP_URL"
) &

export PYTHONPATH="$PROJECT_DIR/backend"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$APP_PORT"
