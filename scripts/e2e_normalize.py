#!/usr/bin/env python3
# Playwright JSON reporter 产物 → 归一化 TSV（E2E 形态对比，issue #410）。
#
#   e2e_normalize.py --input raw.json --output out.tsv --projects chromium,mobile --side baseline
#
# TSV 列：[spec, project, 用例标题, status, 结果, skip 文案]，按行排序以抵消 fullyParallel
# 的非确定顺序。第 4 列是 tests[].status ∈ expected|unexpected|flaky|skipped（passed 永不出现）；
# 第 5 列是 results[-1].status ∈ passed|failed|skipped|timedOut；两列分歧（flaky+passed）是
# retry 洗白不稳定用例的信号——故采集侧必须 --retries=0。
#
# 口径守卫（本工具的病一直是假绿灯，静默产出错误 TSV 比崩掉更坏）：
# - stats 是 reporter 自报的用例总数，对不上即拒绝（须在 project 过滤前比对，stats 含 setup）；
# - 逐 project 校验缺失（--projects 拼错一个仍可能产出半份 TSV 并 exit 0）；
# - setup project 的行在 stats 守卫之后才滤除（它是 chromium/mobile 的 dependencies，
#   --project 滤不掉依赖项目，只能在这里滤）。
import argparse
import json
import os
import sys


def spec_label(node, ancestor_file):
    # spec 列从 spec 节点的 file 字段派生（JSONReportSpec.file 恒存在，
    # 见 frontend/node_modules/playwright/types/testReporter.d.ts）；缺失时回落最近
    # 祖先 suite 的 file，再缺即响亮报错——宁可拒止也不产出 spec 列不明的行。
    file = node.get("file") or ancestor_file
    if not file:
        sys.exit(f"❌ spec 节点缺 file 字段且无祖先 suite 可回落（用例标题: {node.get('title', '?')}）——"
                 "JSON reporter 形态可能已变")
    base = os.path.basename(file)
    return base[: -len(".spec.ts")] if base.endswith(".spec.ts") else base


def collect_rows(data, keep):
    rows = []

    def walk(node, ancestor_file=""):
        if isinstance(node, dict):
            # 同时含 title 与 tests 的节点恰好是 specs[] 元素（suite 节点是
            # title+specs+suites、无 tests），鸭子类型精确命中用例节点。
            # title 在节点自身；tests[] 每 project 一个元素、不含 title
            # （#402：Playwright 1.62 起 t["title"] 必 KeyError）。
            if "file" in node:
                ancestor_file = node["file"]
            if "title" in node and "tests" in node:
                spec = spec_label(node, ancestor_file)
                for t in node["tests"]:
                    status = t.get("status", "?")
                    result = t.get("results", [{}])[-1].get("status", "?")
                    skip_desc = ""
                    for ann in t.get("annotations", []) + t.get("results", [{}])[-1].get("annotations", []):
                        if ann.get("type") == "skip":
                            skip_desc = ann.get("description", "")
                    rows.append([spec, t.get("projectName", "?"), node["title"], status, result, skip_desc])
            for v in node.values():
                walk(v, ancestor_file)
        elif isinstance(node, list):
            for item in node:
                walk(item, ancestor_file)

    walk(data)

    s = data.get("stats", {})
    total = sum(s.get(k, 0) for k in ("expected", "unexpected", "flaky", "skipped"))
    if total != len(rows):
        sys.exit(f"❌ 归一化 {len(rows)} 行 ≠ reporter stats 总数 {total}：JSON reporter 形态可能已变")

    rows = [r for r in rows if r[1] in keep]
    missing = sorted(keep - {r[1] for r in rows})
    if missing:
        sys.exit(f"❌ --projects 里的 {missing} 没产出用例行（project 名拼错？）")
    if not rows:
        sys.exit(f"❌ 没有 --projects {sorted(keep)} 的用例行（--projects 为空？）")
    rows.sort()
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description="Playwright JSON reporter 产物 → 归一化 TSV")
    p.add_argument("--input", required=True, help="playwright JSON reporter 原始产物")
    p.add_argument("--output", required=True, help="输出 TSV 路径（整体重写）")
    p.add_argument("--projects", required=True, help="逗号分隔的 project 名，如 chromium,mobile")
    p.add_argument("--side", required=True, help="采集侧标识（baseline/candidate），仅用于报错定位")
    args = p.parse_args(argv)
    keep = set(args.projects.split(","))

    try:
        with open(args.input) as f:
            data = json.load(f)
    except Exception as e:
        sys.exit(f"❌ {args.input} 不是合法 JSON——playwright 这次运行本身坏了，不是用例 fail：{e}\n"
                 f"   侧: {args.side}；常见原因是 spec 不存在于这一侧，或 webServer/配置坏了")

    rows = collect_rows(data, keep)
    with open(args.output, "w") as f:
        for r in rows:
            f.write("\t".join(r) + "\n")
    print(f"✅ {args.side}: {len(rows)} 行 → {args.output}")


if __name__ == "__main__":
    main()
