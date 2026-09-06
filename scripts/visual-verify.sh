#!/usr/bin/env bash
# ============================================================================
# 前端目检（视觉验证）脚手架
# ============================================================================
# 把「起后端 → 生产构建 → 组装 standalone → 起前端 → 登录 → 截图」这条每次
# 目检都要重走的链路收成一个命令。lint/tsc/build 那套静态门禁仍走
# verify-frontend.sh；本脚本只管一件事：把指定页面在桌面/移动视口下截成图。
#
# 用法（参数原样转给 frontend/scripts/visual-shot.mjs，那里是参数的单一事实来源）：
#   ./scripts/visual-verify.sh                       # 默认双端截三个流水列表（E2E_ACTIVE）
#   ./scripts/visual-verify.sh --path /portfolio/E2E_PORT/positions --device mobile
#   ./scripts/visual-verify.sh --out /tmp/my-shots   # 输出目录（默认 /tmp/visual-verify）
#
# 造数规则与 E2E_ACTIVE 红线见 frontend/AGENTS.md §4「目检」。
# 已监听的后端/前端一律复用：重启后端会清空重灌 /tmp/ir_e2e.db，
# 会打掉操作者手工造的临时数据。
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_URL=http://127.0.0.1:8000
FRONTEND_URL=http://127.0.0.1:3000

# --- Node 自举（同 verify-frontend.sh：非交互 shell 下 .bashrc 提前 return 不加载 nvm）---
if ! command -v node >/dev/null 2>&1; then
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true
  nvm use >/dev/null 2>&1 || true
fi
if ! command -v node >/dev/null 2>&1; then
  echo "❌ 未找到 node。请先: source ~/.bashrc && nvm use"
  exit 1
fi

cd "$REPO_ROOT/frontend"
probe() { curl -fsS -o /dev/null --max-time 3 "$1"; }

wait_up() { # wait_up <url> <日志文件>
  for _ in $(seq 1 60); do
    if probe "$1"; then return 0; fi
    sleep 1
  done
  echo "❌ $1 超时未就绪，看后台日志：$2"
  return 1
}

# --- 1. 后端（:8000，SQLite 临时库 + 种子）---
BACKEND_PID=""
if probe "$BACKEND_URL/health"; then
  echo "✔ 复用已运行的后端 :8000"
else
  echo "→ 启动 E2E 后端（重建 /tmp/ir_e2e.db 并灌种子）"
  nohup python3 "$REPO_ROOT/backend/scripts/run_e2e_backend.py" \
    >/tmp/visual-verify-backend.log 2>&1 &
  BACKEND_PID=$!
  wait_up "$BACKEND_URL/health" /tmp/visual-verify-backend.log
  echo "✔ 后端就绪 (pid $BACKEND_PID)"
fi

# --- 2. 前端（:3000，production standalone，不是 next dev —— 见 frontend/AGENTS.md §4）---
FRONTEND_PID=""
if probe "$FRONTEND_URL"; then
  echo "✔ 复用已运行的前端 :3000（若截图与当前代码不符，先 kill 掉它再重跑）"
else
  if [ ! -x node_modules/.bin/next ]; then
    echo "→ npm ci（依赖安装）"
    npm ci --allow-remote=all
  fi
  echo "→ 生产构建 + 组装 standalone（约 1-2 分钟）"
  NEXT_TELEMETRY_DISABLED=1 npm run build
  # 先删再拷：目标已存在时 cp -r 会拷成 .next/standalone/.next/static/static
  rm -rf .next/standalone/.next/static .next/standalone/public
  cp -r .next/static .next/standalone/.next/static
  cp -r public .next/standalone/public
  nohup env PORT=3000 node .next/standalone/server.js \
    >/tmp/visual-verify-frontend.log 2>&1 &
  FRONTEND_PID=$!
  wait_up "$FRONTEND_URL" /tmp/visual-verify-frontend.log
  echo "✔ 前端就绪 (pid $FRONTEND_PID)"
fi

# --- 3. 截图 ---
# 服务信息先打：截图步骤失败也要能看到 pid，否则留下无人知晓的孤儿进程
if [ -n "$BACKEND_PID$FRONTEND_PID" ]; then
  echo ""
  echo "本脚本启动的后台服务（保持运行：下次目检免启动，playwright webServer 亦复用）"
  echo "  关闭：kill ${BACKEND_PID} ${FRONTEND_PID}"
fi

echo ""
echo "→ 截图（参数说明见 frontend/scripts/visual-shot.mjs 头部）"
node scripts/visual-shot.mjs "$@"
