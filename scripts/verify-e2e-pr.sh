#!/usr/bin/env bash
# ============================================================================
# E2E 归一化对比脚本（纯测试重构 PR 的验证工具）
# ============================================================================
# 用于验证纯测试代码改动（如 #372 helper 收敛、#371 移动端 skip 删除、#383 skip 复核）
# 是否改变了 pass/skip 形态。在 base ref 与 PR 分支两侧各跑一次指定 spec × project 的 E2E，
# 导出归一化 TSV 后 diff。期望空 diff；非空 diff 需人工判读是否为预期变化。
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
# 前置条件（不满足则 1 秒内拒绝，不会先花 10 分钟建栈再报错）：
# - 已 git checkout 到 --branch 指定的分支（构建源要确定性地是 PR 分支 tip）
# - 已跟踪文件无未提交改动（未跟踪文件不拦，含 gitignore 的 e2e/.auth/admin.json）
# - --branch 是本地分支（不接受远端 ref / tag / 游离 HEAD）
#
# 工作原理：
# 1. 前置检查 + 选择隔离端口（默认后端 8100-8199、前端 3100-3199 第一个空闲）
# 2. 启动隔离后端（独立 db 文件，自动种子）
# 3. 构建并启动隔离前端（构建源 = PR 分支 tip；API_BASE_URL 在构建时烘焙）
# 4. 用 git restore 把 frontend/e2e/ 分别换成 base ref 与 PR 分支的内容，各采集一次
#    （HEAD 全程不动，两侧共用同一份构建与同一个后端，唯一变量是 spec）
# 5. 还原 frontend/e2e/，diff 两个 TSV 并报告差异，清理隔离栈（除非 --keep-stack）
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
E2E_SWAPPED=false
SWAP_ORPHANS=""
CUSTOM_LAUNCHER=""

# --- 帮助 ---
show_help() {
  # 范围末尾锚在 set -euo pipefail（全文恰 1 次、紧跟头注释），不是 /^# ====/——
  # 后者在第 4 行（标题框的下边框）就命中，帮助只剩 2 行。
  sed -n '2,/^set -euo pipefail/p' "$0" | sed 's/^# \?//' | head -n -1
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
  # 还原被换过的 frontend/e2e/。必须挂 E2E_SWAPPED 旗标：前置检查失败也会走到 cleanup，
  # 那时无条件 restore --source=HEAD 会把操作者自己在 e2e/ 下的未提交改动静默丢掉。
  # 也必须排在 --keep-stack 的提前 return 之前——留栈不等于留一个被换过的树。
  if [ "$E2E_SWAPPED" = true ]; then
    log "还原 frontend/e2e/ → HEAD ($BRANCH)..."
    git -C "$REPO_ROOT" restore --source=HEAD --worktree --no-overlay -- frontend/e2e/ \
      || log "⚠️  还原失败，手动执行: git -C $REPO_ROOT restore --source=HEAD --worktree -- frontend/e2e/"
    # base 侧独有的 spec 会被写成未跟踪文件（restore --no-overlay 只删已跟踪的），
    # 残留会被后续整包 npx playwright test 捡走，须按预计算的清单显式删掉。
    if [ -n "$SWAP_ORPHANS" ]; then
      while IFS= read -r orphan; do
        [ -n "$orphan" ] && rm -f "$REPO_ROOT/$orphan"
      done <<< "$SWAP_ORPHANS"
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

# --- PHASE 0：前置检查（全部秒级失败，不先花 ~10 分钟建栈再拒）---

# 只接受本地分支：构建源钉在 $BRANCH tip，远端 ref / tag / 游离 HEAD 都不成立
if ! git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  log "❌ 本地分支 $BRANCH 不存在（不接受远端 ref / tag）"
  exit 1
fi

# HEAD 钉在 $BRANCH 上，构建源才确定性地是 PR 分支 tip 而非「操作者碰巧所在的分支」。
# 游离 HEAD 由此第 1 秒即拒（abbrev-ref 得字面量 HEAD，永不等于分支名），全程也无需恢复分支。
CUR_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
if [ "$CUR_BRANCH" != "$BRANCH" ]; then
  log "❌ HEAD 在 $CUR_BRANCH，须先 git checkout $BRANCH（构建源要确定是 PR 分支 tip）"
  exit 1
fi

# 只拦已跟踪改动（未跟踪的不拦，含 gitignore 的 e2e/.auth/admin.json）。必须保持仓库级、
# 不能缩到 frontend/e2e/：构建烘焙 frontend/src、next.config.js、package.json、public，
# 隔离后端 import backend/app 与 backend/tests/seed_base.py——这两处脏了上面那条确定性就静默失效。
if ! git -C "$REPO_ROOT" diff --quiet HEAD; then
  log "❌ 已跟踪文件有未提交改动（构建源会掺入工作树内容）"
  git -C "$REPO_ROOT" diff --stat HEAD
  exit 1
fi

# 基线新鲜度。fetch 失败只降级为警告：离线也必须能用
git -C "$REPO_ROOT" fetch --quiet origin \
  || log "⚠️  fetch 失败（离线？），沿用本地 $BASE_REF，请核对下面 SHA"
BASE_SHA=$(git -C "$REPO_ROOT" rev-parse --short "$BASE_REF")
BRANCH_SHA=$(git -C "$REPO_ROOT" rev-parse --short "$BRANCH")
log "baseline: $BASE_REF @ $BASE_SHA"
log "after:    $BRANCH @ $BRANCH_SHA"
if [ "$BASE_SHA" = "$BRANCH_SHA" ]; then
  log "❌ 两侧指向同一提交，无可对比"
  exit 1
fi

# 「纯测试改动」前提只提示、不硬拦：硬拦实测会误伤 PR #401（它给 ToastContainer.tsx 补了
# data-testid 以支撑 helpers.toastByTitle），那是本工具迄今唯一一次成功实跑。勿改回 exit 1。
if ! git -C "$REPO_ROOT" diff --quiet "$BASE_REF" "$BRANCH" -- frontend/src backend/app; then
  log "⚠️  非纯测试改动——不拦，但这样读 diff："
  git -C "$REPO_ROOT" diff --stat "$BASE_REF" "$BRANCH" -- frontend/src backend/app
  log "   两侧共用同一份 $BRANCH tip 构建与同一个后端，src/backend 改动被两侧同等看到，"
  log "   唯一变量仍是 frontend/e2e/**。若期望的形态变化来自 src 侧，本工具证不了——"
  log "   另跑 npm run test:e2e + scripts/visual-verify.sh。"
fi

# spec 存在性：拼错一个 spec 名不该等 10 分钟才知道
IFS=',' read -ra SPEC_ARR <<< "$SPECS"
for spec in "${SPEC_ARR[@]}"; do
  if [ ! -f "$FRONTEND_DIR/e2e/$spec.spec.ts" ]; then
    log "❌ frontend/e2e/$spec.spec.ts 不存在于 $BRANCH"
    exit 1
  fi
  if ! git -C "$REPO_ROOT" cat-file -e "$BASE_REF:frontend/e2e/$spec.spec.ts" 2>/dev/null; then
    log "⚠️  $spec.spec.ts 不存在于 $BASE_REF——baseline 侧不会产出它的行，diff 会全是新增"
  fi
done

# base 侧独有的 e2e 文件：换 spec 时会被写成未跟踪文件，而 restore --no-overlay 只删已跟踪的，
# 故预计算清单交给 cleanup 精确删除（残留 spec 会被后续整包 playwright test 捡走）。
# 两侧排序与 comm 的比较必须同序（LC_ALL=C），否则 comm 会静默给出错误结果。
SWAP_ORPHANS=$(LC_ALL=C comm -23 \
  <(git -C "$REPO_ROOT" ls-tree -r --name-only "$BASE_REF" -- frontend/e2e/ | LC_ALL=C sort) \
  <(git -C "$REPO_ROOT" ls-tree -r --name-only "$BRANCH" -- frontend/e2e/ | LC_ALL=C sort))
if [ -n "$SWAP_ORPHANS" ]; then
  log "ℹ️  base 侧独有的 e2e 文件（跑完由 cleanup 删除）: $(echo "$SWAP_ORPHANS" | tr '\n' ' ')"
fi

# project 参数展开：--projects 此前是死变量（声明、解析，但从不传给 playwright）。
IFS=',' read -ra PROJ_ARR <<< "$PROJECTS"
PROJ_ARGS=()
for p in "${PROJ_ARR[@]}"; do PROJ_ARGS+=("--project=$p"); done

# --- 端口检测 ---
if [ -z "$BACKEND_PORT" ]; then
  BACKEND_PORT=$(find_free_port 8100 8199) || { log "❌ 8100-8199 无空闲端口"; exit 1; }
fi
if [ -z "$FRONTEND_PORT" ]; then
  FRONTEND_PORT=$(find_free_port 3100 3199) || { log "❌ 3100-3199 无空闲端口"; exit 1; }
fi
log "隔离端口: 后端 :$BACKEND_PORT，前端 :$FRONTEND_PORT"

# --- 换 spec（不换分支）---
# 用 git -C 而非依赖 CWD：capture 阶段 CWD 是 $FRONTEND_DIR，相对 pathspec 会算错。
swap_e2e() {
  log "frontend/e2e/ → $1 ($(git -C "$REPO_ROOT" rev-parse --short "$1"))；期间请勿在本工作树 add/commit"
  git -C "$REPO_ROOT" restore --source="$1" --worktree --no-overlay -- frontend/e2e/
}

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
# 构建源 = $BRANCH tip（PHASE 0 已强制 HEAD == $BRANCH 且已跟踪文件干净）
log "构建隔离前端（API_BASE_URL=http://localhost:$BACKEND_PORT，构建期烘焙）..."
cd "$FRONTEND_DIR"
NEXT_TELEMETRY_DISABLED=1 API_BASE_URL="http://localhost:$BACKEND_PORT" npm run build > "/tmp/e2e-verify-build-$TIMESTAMP.log" 2>&1 \
  || { log "❌ 构建失败，看 /tmp/e2e-verify-build-$TIMESTAMP.log"; exit 1; }
# 先删再拷：目标已存在时 cp -r 会拷成 .next/standalone/.next/static/static
rm -rf .next/standalone/.next/static .next/standalone/public
cp -r .next/static .next/standalone/.next/static
cp -r public .next/standalone/public
log "启动隔离前端..."
# 不传 API_BASE_URL：它是纯构建期变量（只被 next.config.js 消费，rewrite 目标已内联进
# server.js 成字符串字面量），跑起来再传是 no-op，留着会让人误以为成品构建能改指后端。
PORT="$FRONTEND_PORT" node .next/standalone/server.js > "/tmp/e2e-verify-frontend-$TIMESTAMP.log" 2>&1 &
FRONTEND_PID=$!
for i in {1..40}; do
  if curl -sf -o /dev/null -w '%{http_code}' "http://127.0.0.1:$FRONTEND_PORT/" | grep -q "307\|200"; then
    log "前端就绪（pid $FRONTEND_PID）"
    break
  fi
  [ "$i" -eq 40 ] && { log "❌ 前端启动超时"; exit 1; }
  sleep 0.5
done

# --- 采集函数 ---
capture() {
  local output_tsv=$1 ref=$2
  log "采集 → $output_tsv"
  : > "$output_tsv"
  for spec in "${SPEC_ARR[@]}"; do
    raw="/tmp/e2e-verify-raw-$spec-$TIMESTAMP.json"
    runlog="/tmp/e2e-verify-run-$spec-$TIMESTAMP.log"
    # 用例 fail 时 playwright 退出非零，但不中止——fail 状态由 JSON reporter 导出进 TSV、体现在 diff。
    # rc 单独留档是为了把「用例 fail」（合法数据）与「运行本身坏了」区分开。
    # CI="" 钉死 retries=0 / workers 不自适应 / reuseExistingServer=true：操作者环境里
    # export 了 CI=true 的话，retry 会把 flaky 洗成 passed，且会在已占用的隔离端口上硬失败。
    # 注：--project 不会跳过 dependencies，故 setup 仍会跑（归一化器滤掉它那一行）。
    rc=0
    CI="" BASE_URL="http://localhost:$FRONTEND_PORT" npx playwright test "e2e/$spec.spec.ts" \
      "${PROJ_ARGS[@]}" --workers="$WORKERS" --reporter=json > "$raw" 2> "$runlog" || rc=$?
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

# --- 采集 baseline（只换 spec，HEAD 全程不动）---
E2E_SWAPPED=true
swap_e2e "$BASE_REF"
capture "$BASELINE_TSV" "$BASE_REF"

# --- 采集 after ---
# $BRANCH == HEAD 已被 PHASE 0 强制，故这一步同时就是把工作树换回来；
# cleanup 里的 --source=HEAD 还原是对同一不变量的幂等重申（覆盖 Ctrl-C 等路径）。
swap_e2e "$BRANCH"
capture "$AFTER_TSV" "$BRANCH"

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
