#!/usr/bin/env python3
# 归一化 TSV 的形态对比判定（issue #957）。
#
#   e2e_morph_compare.py --base /tmp/base.tsv --candidate /tmp/cand.tsv \
#       [--summary "$GITHUB_STEP_SUMMARY"]
#
# 背景（#957）：此前 e2e-compare 对整份 TSV 做 diff，而第 4/5 列就是运行结果本身
# （`status ∈ expected|unexpected|flaky|skipped`、`结果 ∈ passed|failed|skipped|timedOut`）。
# 于是一条用例的 pass/fail 运行波动就会让形态门禁判红（PR #951 连红 6 轮，两侧归一化
# 行数恒等 718 = 718、差异 100% 落在 mobile），形态门禁被用成了「两次运行结果一致」门禁。
# 本脚本把判据拆成两类：
# - **形态列**（spec / project / 用例标题 / skip 文案 / 是否 skip）：差异 → exit 1，
#   语义与 #410 一致（增删用例、改名、调整 skip；豁免仍由 ci.yml 的 e2e-morph-expected
#   标签分支处理，本脚本不读标签）；
# - **结果列**（status / 结果）：差异只披露（::warning:: + Step Summary），不阻断。
#
# `expected → skipped` 计入形态列（#957 评审补充）：现有 skip 都带文案，状态翻转会同时
# 改动第 6 列，本来就会命中形态列；但将来若出现裸 `test.skip()`（无文案），只按列比对会
# 让「覆盖悄悄消失」降级成 warning。故形态列额外带上「是否 skip」的派生标志。
#
# 顺带消掉的盲区（#957）：capture 步刻意不因用例失败变红，两侧**共同** unexpected 的
# 用例在旧门禁里没有任何 job 点名（共同失败不进 diff）。本脚本把两侧各自的 unexpected
# 清单与计数写进 Step Summary，并对非零计数发 ::warning::。
#
# 退出码：0 = 无形态差异（结果列差异、两侧 unexpected 只披露）；1 = 形态列差异；
# 2 = 输入无效（TSV 缺失/列序不符/空文件/编码坏）或脚本自身崩溃——fail-closed：读不出
# 完整 TSV 绝不退化成空 diff 假绿（同 e2e_normalize.py 的口径守卫立场）。
# rc=1 必须**只**表示「对比完成且存在形态差异」：Python 未捕获异常默认也是 rc=1，若落进
# ci.yml 的 rc=1 豁免分支，坏输入加上 e2e-morph-expected 标签就会静默放行。故 main() 顶层
# 把一切非 SystemExit 异常归到 rc=2（#957 L2 评审）。
import argparse
import sys
import traceback
from typing import NoReturn

#: TSV 列序由 scripts/e2e_normalize.py 固定：spec, project, 用例标题, status, 结果, skip 文案。
COLUMNS = 6
STATUSES = {"expected", "unexpected", "flaky", "skipped"}  # 第 4 列（passed 永不出现）
# 第 4 列的 "?" 是 e2e_normalize.py 的回落哨兵（reporter 形态漂移）：status 决定形态分类，
# 未知即语义不明，必须 rc=2 拒止；第 5 列的 "?" 只影响披露、不参与分类，故容忍。
RESULTS = {"passed", "failed", "skipped", "timedOut", "?"}  # 第 5 列
#: Summary 单节最多列出的条数（Summary 只做可读摘要）；全量清单打到 stdout（job 日志）。
LIST_CAP = 50


def die(message: str) -> NoReturn:
    print(f"❌ {message}", file=sys.stderr)
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(2)


def read_tsv(path: str, side: str) -> list[tuple[str, str, str, str, str, str]]:
    """读一侧 TSV；列数/枚举/编码不符即退出 2——静默产出错误结论比崩掉更坏。"""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeError) as exc:
        # UnicodeDecodeError 是 ValueError 不是 OSError；漏掉它会以 rc=1 崩掉并可能被标签豁免。
        die(f"读不到 {side} 侧 TSV（{path}）：{exc}")
    # 只按 \n 切行：str.splitlines() 还会在 \x0b/\x0c/\u2028 等字符处断开，把标题里的
    # 这类字符撕成两行、误报列数不符（rc=2 不可豁免）。CRLF 容忍靠逐行去掉尾部 \r。
    lines = [line.rstrip("\r") for line in text.split("\n")]
    rows = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        # maxsplit 让 skip 文案里的制表符留在第 6 列；其余列错位会在枚举校验里响亮失败。
        fields = line.split("\t", COLUMNS - 1)
        if len(fields) != COLUMNS:
            die(f"{side} 侧 TSV 第 {lineno} 行有 {len(fields)} 列 ≠ {COLUMNS}（列序变了？）")
        spec, project, title, status, result, skip_desc = fields
        if status not in STATUSES:
            die(f"{side} 侧 TSV 第 {lineno} 行 status={status!r} 不在 {sorted(STATUSES)}")
        if result not in RESULTS:
            die(f"{side} 侧 TSV 第 {lineno} 行 结果={result!r} 不在 {sorted(RESULTS)}")
        rows.append((spec, project, title, status, result, skip_desc))
    if not rows:
        die(f"{side} 侧 TSV 没有任何用例行（{path}）")
    return rows


def classify(rows):
    """→ {身份: [形态列表, 结果列表]}，身份 =(spec, project, 标题)。

    归一化产物每用例一行；仍用集合比对，避免重复行让顺序敏感的 diff 误报。
    """
    groups: dict[tuple[str, str, str], list[list]] = {}
    for spec, project, title, status, result, skip_desc in rows:
        shape, results = groups.setdefault((spec, project, title), ([], []))
        shape.append(("skipped" if status == "skipped" else "run", skip_desc))
        results.append((status, result))
    return groups


def fmt_skip(shape: tuple[str, str]) -> str:
    flag, desc = shape
    if flag != "skipped":
        # 非 skip 行的文案一般为空，但不排除注解在运行用例上：比对键含文案，展示也得含，
        # 否则「只因文案不同」的硬红会渲染成两侧一模一样的 run → run。
        return f"run:{desc}" if desc else "run"
    return f"skipped:{desc}" if desc else "skipped"


def fmt_key(key: tuple[str, str, str]) -> str:
    spec, project, title = key
    return f"{spec}[{project}] {title}"


def fmt_result(result: tuple[str, str]) -> str:
    return f"{result[0]}/{result[1]}"


def diff_sides(base, cand):
    """→ (形态差异行, 结果差异行)。形态差异优先：同一用例二者都变时只记形态。"""
    shape_lines: list[str] = []
    result_lines: list[str] = []
    for key in sorted(set(base) | set(cand)):
        base_shape, base_result = base.get(key, ([], []))
        cand_shape, cand_result = cand.get(key, ([], []))
        if sorted(base_shape) != sorted(cand_shape):
            if not base_shape:
                shape_lines.append(f"+ 仅 candidate：{fmt_key(key)}")
            elif not cand_shape:
                shape_lines.append(f"- 仅 baseline：{fmt_key(key)}")
            else:
                left = " / ".join(sorted(fmt_skip(s) for s in base_shape))
                right = " / ".join(sorted(fmt_skip(s) for s in cand_shape))
                shape_lines.append(f"~ {fmt_key(key)}：baseline[{left}] → candidate[{right}]")
        elif sorted(base_result) != sorted(cand_result):
            left = " / ".join(sorted(fmt_result(r) for r in base_result))
            right = " / ".join(sorted(fmt_result(r) for r in cand_result))
            result_lines.append(f"~ {fmt_key(key)}：baseline[{left}] → candidate[{right}]")
    return shape_lines, result_lines


def unexpected(rows) -> list[str]:
    return [
        f"{fmt_key(row[:3])}：{row[3]}/{row[4]}"
        for row in rows
        if row[3] == "unexpected"
    ]


def section(fh, title: str, lines: list[str]) -> None:
    if not lines:
        return
    fh.write(f"\n#### {title}（{len(lines)} 条）\n\n")
    for line in lines[:LIST_CAP]:
        fh.write(f"- {line}\n")
    if len(lines) > LIST_CAP:
        fh.write(f"- …另有 {len(lines) - LIST_CAP} 条，见 job 日志\n")


def write_summary(path: str, shape_lines, result_lines, base_unexpected, cand_unexpected) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n### E2E 形态对比（形态列阻断 / 结果列披露）\n")
            fh.write(f"\n- 形态列差异：{len(shape_lines)} 处"
                     "（spec/project/标题/skip 文案/是否 skip）→ **阻断**\n")
            fh.write(f"- 结果列差异：{len(result_lines)} 处（status/结果）→ 仅披露\n")
            fh.write(f"- 采集侧 unexpected：baseline {len(base_unexpected)} 条"
                     f" / candidate {len(cand_unexpected)} 条\n")
            section(fh, "形态列差异（阻断）", shape_lines)
            section(fh, "结果列差异（仅披露）", result_lines)
            section(fh, "baseline unexpected", base_unexpected)
            section(fh, "candidate unexpected", cand_unexpected)
    except (OSError, UnicodeError) as exc:
        die(f"写 Step Summary 失败（{path}）：{exc}——披露通道断了不能静默继续")


def main(argv: list[str]) -> int:
    """出入口：崩溃一律归 rc=2，保证 rc=1 只表示「对比完成且存在形态差异」。"""
    try:
        return _run(argv)
    except SystemExit:
        raise  # die() 的 rc=2 与 argparse 的参数错误照常上抛
    except Exception as exc:  # noqa: BLE001——未捕获异常默认 rc=1，会落进标签豁免分支
        try:
            traceback.print_exc()
            print(f"❌ 形态对比脚本异常：{exc!r}", file=sys.stderr)
            print(f"::error::形态对比脚本异常（{type(exc).__name__}: {exc}），"
                  "不适用标签豁免", file=sys.stderr)
        except Exception:  # noqa: BLE001——诊断输出自身失败（管道关闭/编码）也不能逃逸成 rc=1
            pass
        return 2


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="E2E 形态/结果列拆分对比（#957）")
    parser.add_argument("--base", required=True, help="baseline 侧归一化 TSV")
    parser.add_argument("--candidate", required=True, help="candidate 侧归一化 TSV")
    parser.add_argument("--summary", default="", help="Step Summary 文件（可选）")
    args = parser.parse_args(argv)

    base_rows = read_tsv(args.base, "baseline")
    cand_rows = read_tsv(args.candidate, "candidate")
    shape_lines, result_lines = diff_sides(classify(base_rows), classify(cand_rows))
    base_unexpected = unexpected(base_rows)
    cand_unexpected = unexpected(cand_rows)

    # 人读摘要进 stdout（job 日志），**全量不截断**：Summary 超 LIST_CAP 的条目照着
    # 「见 job 日志」来找，日志里就必须真有。工作流命令走 stderr，避免与摘要混排。
    print(f"形态列差异 {len(shape_lines)} 处；结果列差异 {len(result_lines)} 处（仅披露）")
    for line in shape_lines:
        print(f"  {line}")
    print(f"unexpected：baseline {len(base_unexpected)} 条 / candidate {len(cand_unexpected)} 条")
    for title, lines in (("结果列差异（仅披露）", result_lines),
                         ("baseline unexpected", base_unexpected),
                         ("candidate unexpected", cand_unexpected)):
        if lines:
            print(f"{title}（{len(lines)} 条）：")
            for line in lines:
                print(f"  {line}")

    write_summary(args.summary, shape_lines, result_lines, base_unexpected, cand_unexpected)

    if result_lines:
        print(f"::warning::结果列有 {len(result_lines)} 处差异（运行期波动，不阻断）："
              "清单见 Step Summary", file=sys.stderr)
    if base_unexpected or cand_unexpected:
        print(f"::warning::采集侧 unexpected：baseline {len(base_unexpected)} 条"
              f" / candidate {len(cand_unexpected)} 条（清单见 Step Summary）", file=sys.stderr)

    return 1 if shape_lines else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
