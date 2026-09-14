#!/usr/bin/env python3
# Playwright JSON reporter 产物 → flaky 用例汇总（issue #466）。
#
#   e2e_flaky_summary.py --input /tmp/e2e-flaky.json >> "$GITHUB_STEP_SUMMARY"
#
# retries>0 时「失败一次后重试通过」的用例在 HTML 报告里就是 passed——flaky 被静默
# 洗白，与仓库「禁止静默失败」的第一号原则冲突。本脚本把它显性化：清单进 Step Summary，
# 有 flaky 时经 **stderr** 发 ::warning::（stdout 已被调用方重定向到 Step Summary，
# 工作流命令必须走 stderr 才不会被写进报告正文）。
#
# 口径与 scripts/e2e_normalize.py 同一套 JSON reporter 形态假设（issues #402/#410）：
# - 用例节点 = 同时含 title 与 tests 的节点（title 在节点自身；tests[] 不含 title，
#   Playwright 1.62 起 t["title"] 必 KeyError）；
# - tests[].status == 'flaky' = 至少失败一次后最终通过；
# - 形态守卫①：四类之和（expected+unexpected+flaky+skipped）必须等于逐节点收集数；
#   守卫②：0 条用例记录视为异常；守卫③：stats.flaky 与逐节点 flaky 数一致——
#   任一不一致说明 reporter 形态/过滤配置已变，响亮失败（假绿灯比崩掉更坏，同
#   normalizer 的口径守卫）。
#
# 退出码：flaky 本身**不**影响退出码（重试通过不阻断 PR，只要求可见）；文件缺失
# （playwright 没跑到产出阶段，E2E job 自己已经红了）也只提示不失败；只有形态守卫
# 不一致才 exit 非 0。
import argparse
import json
import os
import sys


def collect(data):
    """返回 (flaky 列表, 用例记录总数)。flaky 元素为 (project, 标题, 位置)。"""
    flaky = []
    total = 0

    def walk(node, ancestor_file=""):
        nonlocal total
        if isinstance(node, dict):
            if "file" in node:
                ancestor_file = node["file"]
            if "title" in node and "tests" in node:
                file = node.get("file") or ancestor_file or "?"
                for t in node["tests"]:
                    total += 1
                    if t.get("status") == "flaky":
                        flaky.append((
                            t.get("projectName", "?"),
                            node["title"],
                            f"{file}:{node.get('line', '?')}",
                        ))
            for v in node.values():
                walk(v, ancestor_file)
        elif isinstance(node, list):
            for item in node:
                walk(item, ancestor_file)

    walk(data)
    return flaky, total


def main(argv=None):
    p = argparse.ArgumentParser(description="Playwright JSON reporter 产物 → flaky 汇总")
    p.add_argument("--input", required=True, help="playwright JSON reporter 原始产物")
    args = p.parse_args(argv)

    if not os.path.exists(args.input):
        print("### E2E flaky 汇总")
        print(f"⚠️ 未找到 {args.input}（playwright 未跑到产出阶段），本次无 flaky 数据")
        return

    with open(args.input) as f:
        data = json.load(f)

    flaky, total = collect(data)
    stats = data.get("stats", {})
    # 形态守卫①（与 e2e_normalize.py 同口径）：四类之和必须等于逐节点收集数。
    # 只比 flaky 数会漏掉「节点整片识别不到、恰好 0 flaky」的形态变化（假绿灯）。
    reported_total = sum(stats.get(k, 0) for k in ("expected", "unexpected", "flaky", "skipped"))
    if reported_total != total:
        sys.exit(
            f"❌ 逐节点收集 {total} 条 ≠ reporter stats 总数 {reported_total}："
            "JSON reporter 形态可能已变（口径见 scripts/e2e_normalize.py）"
        )
    # 形态守卫②：一条用例记录都没有 = 运行/过滤配置坏了（reporter 形态变化或
    # --grep/--project 把用例全滤掉）。job 可能仍绿，此处必须响亮失败。
    if total == 0:
        sys.exit(
            "❌ reporter 产出 0 条用例记录：E2E 形态或过滤配置异常（静默丢覆盖），"
            "请检查 playwright 配置与本步骤的调用参数"
        )
    reported = stats.get("flaky", 0)
    if reported != len(flaky):
        sys.exit(
            f"❌ stats.flaky={reported} ≠ 逐节点收集 {len(flaky)}："
            "JSON reporter 形态可能已变（口径见 scripts/e2e_normalize.py）"
        )

    print("### E2E flaky 汇总（retries 后转绿：不阻断，但不可静默）")
    if not flaky:
        print(f"- ✅ 无 flaky 用例（本次 {total} 条用例记录）")
        return
    print(f"- ⚠️ **{len(flaky)} 个用例 flaky**（至少失败一次后重试通过）")
    print()
    print("| project | 用例 | 位置 |")
    print("| --- | --- | --- |")
    for project, title, where in sorted(flaky):
        print(f"| {project} | {title} | `{where}` |")
    print(
        f"::warning::{len(flaky)} 个 E2E 用例 flaky（失败后重试通过），清单见 Step Summary",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
