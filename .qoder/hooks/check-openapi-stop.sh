#!/bin/bash
# Stop hook：后端 router/schema 改动后 openapi.json 漂移时，阻塞 Agent 停止并要求重新导出契约。
# 门禁本体：backend/check_openapi.py（与 CI cli-contract-check 同口径）。
input=$(cat)
PROJECT_DIR="${QODER_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$PROJECT_DIR" || exit 0

# 已由 Stop hook 驱动的延续轮次：放行，避免死循环
if [ "$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ]; then
  exit 0
fi

PY="$PROJECT_DIR/.venv-openapi/bin/python"
[ -x "$PY" ] || exit 0  # 钉版环境缺失时不阻塞（CI 兜底）

# 触发判定：相对 origin/main，契约相关源码（routers/schemas/main.py）有改动才跑门禁
changed=$(git diff --name-only origin/main...HEAD 2>/dev/null; git diff --name-only HEAD 2>/dev/null)
echo "$changed" | grep -Eq '^backend/app/(routers|schemas|main\.py)' || exit 0

output=$(SECRET_KEY="$(openssl rand -hex 32)" DEBUG=false "$PY" backend/check_openapi.py 2>&1)
rc=$?
if [ $rc -ne 0 ]; then
  {
    echo "openapi.json 与后端代码漂移（backend/check_openapi.py 未通过）。请先重新导出契约再继续："
    echo "  1. cd backend && SECRET_KEY=\$(openssl rand -hex 32) ../.venv-openapi/bin/python export_openapi.py"
    echo "  2. SECRET_KEY=\$(openssl rand -hex 32) ../.venv-openapi/bin/python ../ir-cli/scripts/gen_response_fields.py"
    echo "  3. 复核 backend/openapi.json 与 ir-cli/ir_cli/response_fields.py 的 diff 后，随本次改动一并提交"
    echo ""
    echo "门禁输出："
    echo "$output" | head -40
  } >&2
  exit 2
fi

exit 0
