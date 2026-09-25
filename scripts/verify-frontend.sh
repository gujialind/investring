#!/usr/bin/env bash
# ============================================================================
# 前端本地验证脚本（推送前门禁）
# ============================================================================
# 与 CI 的 "Frontend Lint & Build" job 的本地四步同口径：
#   ESLint → TypeScript 类型检查 → 单元测试（Vitest，issue #253）→ next build
# （该 job 另有 PR-only 的增量覆盖率门禁 diff-cover，本地不跑、复现命令见
#   frontend/AGENTS.md §3；issue #485）
# 本地通过则 CI 前端门禁基本不会挂，避免推送后浪费一轮 CI。
#
# 用法：
#   ./scripts/verify-frontend.sh          # 完整门禁
#   ./scripts/verify-frontend.sh --quick  # 仅 lint + tsc + 单测（跳过耗时的 build）
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/../frontend"

# --- Node 自举：非交互 shell 下 .bashrc 提前 return 不加载 nvm，手动 source ---
if ! command -v node >/dev/null 2>&1 || [ ! -x "$(command -v node)" ]; then
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true
  nvm use >/dev/null 2>&1 || true
fi
# 仍找不到时回落：直接挂上已安装的最新版本（无需 default alias）
if ! command -v node >/dev/null 2>&1 && [ -d "$HOME/.nvm/versions/node" ]; then
  LATEST=$(ls "$HOME/.nvm/versions/node" | sort -V | tail -1)
  export PATH="$HOME/.nvm/versions/node/$LATEST/bin:$PATH"
fi

# --- Node 版本检查（.nvmrc 要求 24；谨防误用 Windows 侧 node） ---
if ! command -v node >/dev/null 2>&1; then
  echo "❌ 未找到 node。请先: source ~/.bashrc && nvm use"
  exit 1
fi
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
if [ "$NODE_MAJOR" -lt 24 ]; then
  echo "❌ Node 版本过低（当前 $(node -v)，要求 >= 24）。请: nvm use"
  exit 1
fi
case "$(command -v node)" in
  /mnt/c/*) echo "❌ 检测到 Windows 侧 node，请 nvm use 切换到 WSL 原生 Node"; exit 1 ;;
esac
echo "✔ Node $(node -v)"

# --- 依赖检查（#577 ①：lock 的 resolved host 已归一为 registry.npmjs.org；本机 registry 若配成
#     镜像，npm ≥12 会把 npmjs tarball 视为 remote 而拒拉（EALLOWREMOTE），故保留
#     --allow-remote=all 兼容。该 flag 只属于本脚本的本地兼容面，CI 安装步骤仍是裸
#     npm ci；防复发守门已落地：scripts/tests/test_lock_registry.py（#577 ③）钉住
#     lock 的外来 host 与 CI 安装步骤形态） ---
if [ ! -x node_modules/.bin/next ]; then
  echo "→ npm ci（依赖安装）"
  npm ci --allow-remote=all
fi

QUICK=false
[ "${1:-}" = "--quick" ] && QUICK=true

mkdir -p ../.cache
exec 9>../.cache/frontend-build.lock
flock -n 9 || { echo "另一个构建或本地验证正在使用本 worktree" >&2; exit 2; }

echo ""
echo "=== [1/$([ "$QUICK" = true ] && echo 3 || echo 4)] ESLint ==="
npm run lint

echo ""
echo "=== [2/$([ "$QUICK" = true ] && echo 3 || echo 4)] TypeScript 类型检查 ==="
npx tsc --noEmit

echo ""
echo "=== [3/$([ "$QUICK" = true ] && echo 3 || echo 4)] 单元测试（Vitest） ==="
npm run test

if [ "$QUICK" = true ]; then
  echo ""
  echo "✅ quick 模式通过（lint + tsc + 单测）"
  exit 0
fi

echo ""
echo "=== [4/4] Next.js 生产构建 ==="
NEXT_TELEMETRY_DISABLED=1 npm run build

echo ""
echo "✅ 前端门禁全部通过，与 CI 同口径，可以推送"
