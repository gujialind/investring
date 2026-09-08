#!/usr/bin/env bash
# ============================================================================
# E2E 归一化对比脚本（纯测试重构 PR 的验证工具）
# ============================================================================
# 用于验证纯测试代码改动（如 #372 helper 收敛、#371 移动端 skip 删除、#383 skip 复核）
# 是否改变了 pass/skip 形态。在 base 分支和 PR 分支各跑一次指定 spec × project 的 E2E，
# 导出归一化 TSV 后 diff，期望空 diff 或指定的单行变化。
#
# 用法：
#   ./scripts/verify-e2e-pr.sh --branch feature/383-skip-review
#   ./scripts/verify-e2e-pr.sh --branch feature/371-e2e-mobile-false-skip \
#       --specs platform-select-search,datepicker-in-dialog
#   ./scripts/verify-e2e-pr.sh --branch feature/372-e2e-select-helpers \
#       --workers 2 --keep-stack
#
# 参数：
#   --branch <name>        PR 分支名（必填）
#   --base-ref <ref>       对比基线，默认 origin/main
#   --specs <list>         逗号分隔的 spec 名（不含 .spec.ts 后缀），默认 4 个受影响 spec
#   --projects <list>      逗号分隔的 project 名，默认 chromium,mobile
#   --workers <n>          Playwright workers 数，默认 1（消除 CPU 争用非确定性）
#   --backend-port <n>     隔离后端端口，默认自动检测（8100-8199 范围第一个空闲）
#   --frontend-port <n>    隔离前端端口，默认自动检测（3100-3199 范围第一个空闲）
#   --keep-stack           跑完后保留隔离栈（便于手动复核），默认跑完清理
#   --help                 显示帮助
#
# 工作原理：
# 1. 检测并发会话占用的端口（:3000/:8000），自动选择安全端口避免冲突
# 2. 启动隔离后端（独立 db 文件，自动种子）
# 3. 构建并启动隔离前端（API_BASE_URL 在构建时烘焙）
# 4. 在 base 分支采集 baseline（4 spec × 2 project，导出归一化 TSV）
# 5. 切换到 PR 分支，采集 after
# 6. diff 两个 TSV 并报告差异
# 7. 恢复原始分支，清理隔离栈（除非 --keep-stack）
#
# 隔离栈：
# - 后端：127.0.0.1:<backend-port>，db 文件 /tmp/ir_e2e_verify_<pid>.db
# - 前端：127.0.0.1:<frontend-port>，生产 standalone 构建
# - 与默认 :3000/:8000 完全隔离，不影响并发会话
#
# 输出：
# - /tmp/e2e-verify-baseline-<timestamp>.tsv
# - /tmp/e2e-verify-after-<timestamp>.tsv
# - /tmp/e2e-verify-raw-<spec>-<timestamp>.json   每个 spec 的 playwright JSON reporter 原始产物
# - /tmp/e2e-verify-run-<spec>-<timestamp>.log    每个 spec 的 playwright stderr（归一化失败时先看这个）
# - /tmp/e2e-verify-{backend,frontend,build}-<timestamp>.log
# - diff 结果（stdout）
#
# ⚠️ 勿经管道判定成败：`./verify-e2e-pr.sh ... | tee out.log` 拿到的是 tee 的退出码 0，
#    脚本内的 set -o pipefail 管不到调用方的管道。要留档就重定向：`... > out.log 2>&1`。
# ============================================================================
set -euo pipefail

# --- 默认参数 ---
BRANCH=""
BASE_REF="origin/main"
DEFAULT_SPECS="platform-select-search,product-select-market,trade-buy-amount-linkage,datepicker-in-dialog"
SPECS="$DEFAULT_SPECS"
DEFAULT_PROJECTS="chromium,mobile"
PROJECTS="$DEFAULT_PROJECTS"
WORKERS=1
BACKEND_PORT=""
FRONTEND_PORT=""
KEEP_STACK=false
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
BACKEND_LAUNCHER="$REPO_ROOT/backend/scripts/run_e2e_backend.py"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BASELINE_TSV="/tmp/e2e-verify-baseline-$TIMESTAMP.tsv"
AFTER_TSV="/tmp/e2e-verify-after-$TIMESTAMP.tsv"
ISOLATED_DB=""
BACKEND_PID=""
FRONTEND_PID=""
ORIGINAL_BRANCH=""
CUSTOM_LAUNCHER=""

# --- 帮助 ---
show_help() {
  sed -n '2,/^# ====/p' "$0" | sed 's/^# \?//' | head -n -1
  exit 0
}

# --- 参数解析 ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2 ;;
    --base-ref) BASE_REF="$2"; shift 2 ;;
    --specs) SPECS="$2"; shift 2 ;;
    --projects) PROJECTS="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --backend-port) BACKEND_PORT="$2"; shift 2 ;;
    --frontend-port) FRONTEND_PORT="$2"; shift 2 ;;
    --keep-stack) KEEP_STACK=true; shift ;;
    --help) show_help ;;
    *) echo "❌ 未知参数: $1"; show_help ;;
  esac
done

[ -z "$BRANCH" ] && { echo "❌ 必须指定 --branch"; show_help; }

# --- 工具函数 ---
log() { echo "[$(date '+%H:%M:%S')] $*"; }

CLEANUP_DONE=false
cleanup() {
  [ "$CLEANUP_DONE" = true ] && return
  CLEANUP_DONE=true
  # 失败/中断路径兜底：恢复原始分支（正常路径已恢复时为 no-op）
  if [ -n "$ORIGINAL_BRANCH" ] && [ "$ORIGINAL_BRANCH" != "HEAD" ]; then
    local cur
    cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
    if [ "$cur" != "$ORIGINAL_BRANCH" ]; then
      log "恢复原始分支 ($ORIGINAL_BRANCH)..."
      git checkout "$ORIGINAL_BRANCH" --quiet || log "⚠️ 分支恢复失败，请手动执行: git checkout $ORIGINAL_BRANCH"
    fi
  fi
  if [ "$KEEP_STACK" = true ]; then
    log "隔离栈保留（--keep-stack）：后端 :$BACKEND_PORT，前端 :$FRONTEND_PORT，db $ISOLATED_DB"
    return
  fi
  log "清理隔离栈..."
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$ISOLATED_DB" ] && [ -f "$ISOLATED_DB" ] && rm -f "$ISOLATED_DB"
  [ -n "$CUSTOM_LAUNCHER" ] && [ -f "$CUSTOM_LAUNCHER" ] && rm -f "$CUSTOM_LAUNCHER"
  log "隔离栈已清理"
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

port_in_use() {
  ss -tuln 2>/dev/null | grep -q ":$1 " || netstat -tuln 2>/dev/null | grep -q ":$1 "
}

find_free_port() {
  local start=$1 end=$2
  for ((port=start; port<=end; port++)); do
    if ! port_in_use "$port"; then
      echo "$port"
      return 0
    fi
  done
  return 1
}

# --- 保存当前分支 ---
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)
log "当前分支: $ORIGINAL_BRANCH"

# --- 端口检测 ---
if [ -z "$BACKEND_PORT" ]; then
  BACKEND_PORT=$(find_free_port 8100 8199) || { log "❌ 8100-8199 无空闲端口"; exit 1; }
fi
if [ -z "$FRONTEND_PORT" ]; then
  FRONTEND_PORT=$(find_free_port 3100 3199) || { log "❌ 3100-3199 无空闲端口"; exit 1; }
fi
log "隔离端口: 后端 :$BACKEND_PORT，前端 :$FRONTEND_PORT"

# --- 检查并发会话 ---
if port_in_use 3000 || port_in_use 8000; then
  log "⚠️  检测到 :3000 或 :8000 被占用（并发会话），将使用隔离端口"
fi

# --- 启动隔离后端 ---
log "启动隔离后端..."
ISOLATED_DB="/tmp/ir_e2e_verify_$$.db"
CUSTOM_LAUNCHER="/tmp/run_e2e_backend_verify_$$.py"
cat > "$CUSTOM_LAUNCHER" <<PYEOF
import os, sys
from pathlib import Path
from contextlib import asynccontextmanager

BACKEND_DIR = Path("$REPO_ROOT/backend").resolve()
os.environ.update(
    DATABASE_URL="sqlite:///$ISOLATED_DB",
    SECRET_KEY="test-secret-key-e2e",
    SCHEDULER_ENABLED="false",
    DEBUG="true",
)
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

if os.path.exists("$ISOLATED_DB"):
    os.remove("$ISOLATED_DB")

from app.main import app
from app.database import SessionLocal
from tests.seed_base import seed_base_data, seed_e2e_active

db = SessionLocal()
try:
    seed_base_data(db)
    seed_e2e_active(db)
finally:
    db.close()

@asynccontextmanager
async def _noop_lifespan(app):
    yield

app.router.lifespan_context = _noop_lifespan

import uvicorn
uvicorn.run(app, host="127.0.0.1", port=$BACKEND_PORT, log_level="warning")
PYEOF
cd "$REPO_ROOT"
python3 "$CUSTOM_LAUNCHER" > "/tmp/e2e-verify-backend-$TIMESTAMP.log" 2>&1 &
BACKEND_PID=$!
for i in {1..40}; do
  if curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null; then
    log "后端就绪（pid $BACKEND_PID）"
    break
  fi
  [ "$i" -eq 40 ] && { log "❌ 后端启动超时"; exit 1; }
  sleep 0.5
done

# --- 构建并启动隔离前端 ---
log "构建隔离前端（API_BASE_URL=http://localhost:$BACKEND_PORT）..."
cd "$FRONTEND_DIR"
NEXT_TELEMETRY_DISABLED=1 API_BASE_URL="http://localhost:$BACKEND_PORT" npm run build > "/tmp/e2e-verify-build-$TIMESTAMP.log" 2>&1
cp -r .next/static .next/standalone/.next/static
cp -r public .next/standalone/public
log "启动隔离前端..."
PORT="$FRONTEND_PORT" API_BASE_URL="http://localhost:$BACKEND_PORT" node .next/standalone/server.js > "/tmp/e2e-verify-frontend-$TIMESTAMP.log" 2>&1 &
FRONTEND_PID=$!
for i in {1..40}; do
  if curl -sf -o /dev/null -w '%{http_code}' "http://127.0.0.1:$FRONTEND_PORT/" | grep -q "307\|200"; then
    log "前端就绪（pid $FRONTEND_PID）"
    break
  fi
  [ "$i" -eq 40 ] && { log "❌ 前端启动超时"; exit 1; }
  sleep 0.5
done

# --- 检查未提交改动 ---
if [ -n "$(git status --porcelain)" ]; then
  log "❌ 工作目录有未提交改动，请先提交或 stash"
  git status --short
  exit 1
fi

# --- 检查分支存在 ---
if ! git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  log "❌ 分支 $BRANCH 不存在"
  exit 1
fi

# --- 采集函数 ---
capture() {
  local output_tsv=$1 ref=$2
  log "采集 → $output_tsv"
  : > "$output_tsv"
  IFS=',' read -ra SPEC_ARR <<< "$SPECS"
  for spec in "${SPEC_ARR[@]}"; do
    raw="/tmp/e2e-verify-raw-$spec-$TIMESTAMP.json"
    runlog="/tmp/e2e-verify-run-$spec-$TIMESTAMP.log"
    # 用例 fail 时 playwright 退出非零，但不中止——fail 状态由 JSON reporter 导出进 TSV、体现在 diff。
    # rc 单独留档是为了把「用例 fail」（合法数据）与「运行本身坏了」区分开。
    # CI="" 钉死 retries=0 / workers 不自适应 / reuseExistingServer=true：操作者环境里
    # export 了 CI=true 的话，retry 会把 flaky 洗成 passed，且会在已占用的隔离端口上硬失败。
    rc=0
    CI="" BASE_URL="http://localhost:$FRONTEND_PORT" npx playwright test "e2e/$spec.spec.ts" \
      --workers="$WORKERS" --reporter=json > "$raw" 2> "$runlog" || rc=$?
    log "  $spec: playwright 退出码 $rc（非 0 = 有用例未通过，状态已进 TSV）"
    python3 - "$raw" "$spec" "$output_tsv" "$runlog" "$PROJECTS" "$ref" <<'PYEOF'
import json, sys
raw, spec, out, runlog, projects, ref = sys.argv[1:7]
keep = set(projects.split(','))
try:
    with open(raw) as f:
        data = json.load(f)
except Exception as e:
    sys.exit(f"❌ {raw} 不是合法 JSON——playwright 这次运行本身坏了，不是用例 fail：{e}\n"
             f"   侧: {ref}；常见原因是该 spec 不存在于这一侧，或 webServer/配置坏了\n"
             f"   看运行日志：{runlog}")

rows = []
def walk(node):
    if isinstance(node, dict):
        # Playwright JSON reporter：同时含 title 与 tests 的节点恰好是 specs[] 元素
        # （suite 节点是 title+specs+suites、无 tests），故此鸭子类型精确命中用例节点。
        # title 在节点自身；tests[] 每 project 一个元素、不含 title（原 t["title"] 必 KeyError）。
        if "title" in node and "tests" in node:
            for t in node["tests"]:
                status = t.get("status", "?")
                result = t.get("results", [{}])[-1].get("status", "?")
                skip_desc = ""
                for ann in t.get("annotations", []) + t.get("results", [{}])[-1].get("annotations", []):
                    if ann.get("type") == "skip":
                        skip_desc = ann.get("description", "")
                rows.append([spec, t.get("projectName", "?"), node["title"], status, result, skip_desc])
        for v in node.values():
            walk(v)
    elif isinstance(node, list):
        for item in node:
            walk(item)
walk(data)

# 口径守卫：stats 是 reporter 自报的用例总数，对不上就说明形态漂了或漏了节点——
# 静默产出一份行数不对的 TSV 比崩掉更坏（本工具的病一直是假绿灯）。此检查须在
# project 过滤之前：stats 把 setup 也算在内。
s = data.get("stats", {})
total = sum(s.get(k, 0) for k in ("expected", "unexpected", "flaky", "skipped"))
if total != len(rows):
    sys.exit(f"❌ 归一化 {len(rows)} 行 ≠ reporter stats 总数 {total}：JSON reporter 形态可能已变，看 {raw}")

# setup 是 chromium/mobile 的 dependencies，每次采集都会跑并产出一行；它不是被测用例，
# 且 --project 过滤不掉依赖项目（要 --no-deps，但那样就没有 storageState 了），只能在这里滤。
rows = [r for r in rows if r[1] in keep]
# 逐个 project 校验：只查「过滤后为空」会漏掉部分命中——--projects chromiun,mobile 拼错一个，
# 仍能靠 mobile 产出半份 TSV 并 exit 0，操作者会把空 diff 读成「两个 project 都一致」。
missing = sorted(keep - {r[1] for r in rows})
if missing:
    sys.exit(f"❌ --projects 里的 {missing} 在 {raw} 中没产出用例行（project 名拼错？侧: {ref}）")
if not rows:
    sys.exit(f"❌ {raw} 里没有 --projects {sorted(keep)} 的用例行（--projects 为空？侧: {ref}）")
rows.sort()
with open(out, "a") as f:
    for r in rows:
        f.write("\t".join(r) + "\n")
PYEOF
  done
  log "采集完成: $(wc -l < "$output_tsv") 行"
}

# --- 采集 baseline ---
log "切换到 base ($BASE_REF)..."
git checkout "$BASE_REF" --quiet
capture "$BASELINE_TSV" "$BASE_REF"

# --- 采集 after ---
log "切换到 PR 分支 ($BRANCH)..."
git checkout "$BRANCH" --quiet
capture "$AFTER_TSV" "$BRANCH"

# --- 恢复原始分支 ---
log "恢复到原始分支 ($ORIGINAL_BRANCH)..."
git checkout "$ORIGINAL_BRANCH" --quiet

# --- Diff 报告 ---
log "=== DIFF ==="
if diff "$BASELINE_TSV" "$AFTER_TSV"; then
  log "✅ DIFF 为空（pass/skip 形态完全一致）"
else
  log "⚠️  DIFF 非空（见上）"
  log "baseline: $(wc -l < "$BASELINE_TSV") 行"
  log "after:    $(wc -l < "$AFTER_TSV") 行"
  log "状态分布 (baseline): $(cut -f5 "$BASELINE_TSV" | sort | uniq -c | tr '\n' ' ')"
  log "状态分布 (after):    $(cut -f5 "$AFTER_TSV" | sort | uniq -c | tr '\n' ' ')"
fi

log "输出文件:"
log "  baseline: $BASELINE_TSV"
log "  after:    $AFTER_TSV"
log "  后端日志: /tmp/e2e-verify-backend-$TIMESTAMP.log"
log "  前端日志: /tmp/e2e-verify-frontend-$TIMESTAMP.log"
log "✅ 验证完成"
