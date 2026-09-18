"""
openapi.json 漂移门禁（issue #170，#539 隔离强化）：隔离子进程生成的 OpenAPI schema
与已提交 backend/openapi.json 比对。退出码：0 一致、1 漂移、2 检查执行失败。

改 router/schema 后必须重新导出 openapi.json，否则本脚本 exit 1：
  1. python backend/export_openapi.py --offline
  2. python ir-cli/scripts/gen_response_fields.py
  3. 提交 backend/openapi.json 与 ir-cli/ir_cli/response_fields.py

使用方式（从仓库根运行，无需起服务）：
  python backend/check_openapi.py

数据库隔离（#539）：子进程不读取调用方 .env、不继承应用环境变量；
每次使用独立临时 SQLite，强制关闭调度，结束后清理全部临时资源。
"""
import json
import sys

from openapi_runtime import OPENAPI_PATH, generate_schema, validate_schema


def normalize(spec: dict) -> str:
    """规范化序列化：key 排序，消除字段顺序差异。"""
    return json.dumps(spec, ensure_ascii=False, sort_keys=True)


def main() -> int:
    try:
        with open(OPENAPI_PATH, encoding="utf-8") as f:
            committed = validate_schema(json.load(f))
        generated = generate_schema()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[error] OpenAPI 检查执行失败：{exc}", file=sys.stderr)
        return 2

    if normalize(generated) == normalize(committed):
        print("[ok] backend/openapi.json 与隔离子进程生成的 schema 一致")
        return 0

    # 输出首批差异点辅助定位
    g_paths = generated.get("paths", {})
    c_paths = committed.get("paths", {})
    diffs = []
    for p in sorted(set(g_paths) - set(c_paths)):
        diffs.append(f"新增 path（openapi.json 缺失）: {p}")
    for p in sorted(set(c_paths) - set(g_paths)):
        diffs.append(f"多余 path（后端已不存在）: {p}")
    for p in sorted(set(g_paths) & set(c_paths)):
        if normalize(g_paths[p]) != normalize(c_paths[p]):
            diffs.append(f"path 定义不一致: {p}")

    print("[error] backend/openapi.json 与后端代码漂移：", file=sys.stderr)
    for d in (diffs[:20] or ["(未检出具体 path 差异，可能为 info/components 字段漂移)"]):
        print(f"  - {d}", file=sys.stderr)
    print(
        "\n请重新导出并提交：\n"
        "  1. python backend/export_openapi.py --offline\n"
        "  2. python ir-cli/scripts/gen_response_fields.py\n"
        "  3. 提交 backend/openapi.json 与 ir-cli/ir_cli/response_fields.py",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
