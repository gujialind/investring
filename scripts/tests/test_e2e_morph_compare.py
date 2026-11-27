# scripts/e2e_morph_compare.py 的单测（issue #957）。
# 判定拆两类是本单的核心语义：形态列差异阻断、结果列差异只披露。这里用合成 TSV
# 把「哪种差异会红」逐条钉住——否则拆分一旦漂移，门禁要么重新被抖动打红，要么
# 静默放走用例增删，两种都不会让 ci.yml 之外的任何地方变红。
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import e2e_morph_compare


def write_tsv(path: Path, rows: list[tuple[str, ...]]) -> Path:
    path.write_text("\n".join("\t".join(row) for row in rows) + "\n", encoding="utf-8")
    return path


def run(tmp_path, base_rows, cand_rows, summary=True):
    base = write_tsv(tmp_path / "base.tsv", base_rows)
    cand = write_tsv(tmp_path / "cand.tsv", cand_rows)
    report = tmp_path / "summary.md"
    argv = ["--base", str(base), "--candidate", str(cand)]
    if summary:
        argv += ["--summary", str(report)]
    rc = e2e_morph_compare.main(argv)
    return rc, report.read_text(encoding="utf-8") if summary else ""


def row(spec="demo", project="chromium", title="用例A",
        status="expected", result="passed", skip_desc=""):
    return (spec, project, title, status, result, skip_desc)


def test_identical_sides_are_green(tmp_path):
    rc, summary = run(tmp_path, [row()], [row()])
    assert rc == 0
    assert "形态列差异：0 处" in summary
    assert "结果列差异：0 处" in summary


def test_result_column_fluctuation_does_not_block(tmp_path, capsys):
    """#957 的主场景：用例 pass/fail 互换不阻断，但要在 Summary 里逐条披露。"""
    rc, summary = run(
        tmp_path,
        [row(status="expected", result="passed")],
        [row(status="unexpected", result="failed")],
    )
    assert rc == 0, "结果列差异不得阻断——这正是 PR #951 连红 6 轮的成因"
    assert "结果列差异：1 处" in summary
    assert "baseline[expected/passed] → candidate[unexpected/failed]" in summary
    assert "::warning::结果列有 1 处差异" in capsys.readouterr().err


def test_added_test_blocks(tmp_path):
    """形态门禁的本职：增删用例必须硬红，不因拆列被静默放过。"""
    rc, summary = run(tmp_path, [row()], [row(), row(title="新增用例")])
    assert rc == 1
    assert "形态列差异：1 处" in summary
    assert "仅 candidate：demo[chromium] 新增用例" in summary


def test_removed_test_blocks(tmp_path):
    rc, summary = run(tmp_path, [row(), row(title="删掉的用例")], [row()])
    assert rc == 1
    assert "仅 baseline：demo[chromium] 删掉的用例" in summary


def test_skip_description_change_blocks(tmp_path):
    skipped = dict(status="skipped", result="skipped")
    rc, summary = run(
        tmp_path,
        [row(skip_desc="移动端无入口", **skipped)],
        [row(skip_desc="移动端已废弃", **skipped)],
    )
    assert rc == 1
    assert "skipped:移动端无入口" in summary and "skipped:移动端已废弃" in summary


def test_bare_skip_transition_blocks(tmp_path):
    """无文案的 test.skip() 让状态翻转只落结果列；「是否 skip」派生标志必须把它留在形态列。"""
    rc, summary = run(
        tmp_path,
        [row(status="expected", result="passed")],
        [row(status="skipped", result="skipped", skip_desc="")],
    )
    assert rc == 1, "expected→skipped 是覆盖变化，不能降级成 warning（#957 评审补充）"
    assert "形态列差异：1 处" in summary


def test_unskip_transition_blocks(tmp_path):
    rc, _ = run(
        tmp_path,
        [row(status="skipped", result="skipped", skip_desc="临时跳过")],
        [row(status="expected", result="passed")],
    )
    assert rc == 1


def test_both_sides_unexpected_is_visible(tmp_path, capsys):
    """旧门禁的盲区：两侧共同 unexpected 不进 diff、没有任何 job 点名（#957）。"""
    failed = dict(status="unexpected", result="failed")
    rc, summary = run(tmp_path, [row(**failed)], [row(**failed)])
    assert rc == 0
    assert "采集侧 unexpected：baseline 1 条 / candidate 1 条" in summary
    err = capsys.readouterr().err
    assert "::warning::采集侧 unexpected：baseline 1 条 / candidate 1 条" in err


def test_unexpected_listing_names_the_case(tmp_path):
    failed = dict(status="unexpected", result="failed")
    _, summary = run(tmp_path, [row(**failed)], [row(), row(**failed)])
    assert "#### baseline unexpected（1 条）" in summary
    assert "#### candidate unexpected（1 条）" in summary
    assert "- demo[chromium] 用例A：unexpected/failed" in summary


def test_skip_description_may_contain_tab(tmp_path):
    """skip 文案里的制表符留在第 6 列（maxsplit），不因列数守卫误杀。"""
    base = row(status="skipped", result="skipped", skip_desc="原因\t附注")
    rc, _ = run(tmp_path, [base], [base])
    assert rc == 0


def test_row_order_is_irrelevant(tmp_path):
    rc, _ = run(
        tmp_path,
        [row(title="A"), row(title="B")],
        [row(title="B"), row(title="A")],
    )
    assert rc == 0


def test_summary_is_appended(tmp_path):
    base = write_tsv(tmp_path / "base.tsv", [row()])
    cand = write_tsv(tmp_path / "cand.tsv", [row()])
    report = tmp_path / "summary.md"
    report.write_text("先前步骤的摘要\n", encoding="utf-8")
    e2e_morph_compare.main(["--base", str(base), "--candidate", str(cand),
                            "--summary", str(report)])
    text = report.read_text(encoding="utf-8")
    assert text.startswith("先前步骤的摘要\n"), "Summary 是多步共用文件，只能追加"
    assert "### E2E 形态对比" in text


def assert_loud(argv):
    with pytest.raises(SystemExit) as exc:
        e2e_morph_compare.main(argv)
    assert exc.value.code == 2, "输入无效必须 fail-closed（rc=2），不能退化成空 diff 假绿"


def test_missing_file_is_loud(tmp_path):
    assert_loud(["--base", str(tmp_path / "nope.tsv"),
                 "--candidate", str(tmp_path / "nope.tsv")])


def test_wrong_column_count_is_loud(tmp_path):
    base = write_tsv(tmp_path / "base.tsv", [("demo", "chromium", "用例A", "expected")])
    cand = write_tsv(tmp_path / "cand.tsv", [row()])
    assert_loud(["--base", str(base), "--candidate", str(cand)])


def test_unknown_status_is_loud(tmp_path):
    base = write_tsv(tmp_path / "base.tsv", [row()])
    cand = write_tsv(tmp_path / "cand.tsv", [row(status="passed", result="passed")])
    assert_loud(["--base", str(base), "--candidate", str(cand)])


def test_empty_tsv_is_loud(tmp_path):
    base = tmp_path / "base.tsv"
    base.write_text("", encoding="utf-8")
    cand = write_tsv(tmp_path / "cand.tsv", [row()])
    assert_loud(["--base", str(base), "--candidate", str(cand)])


def test_invalid_utf8_is_loud(tmp_path, capsys):
    """UnicodeDecodeError 不是 OSError：漏掉它会以 rc=1 崩掉、被标签当成形态差异放行。"""
    base = tmp_path / "base.tsv"
    base.write_bytes(b"demo\tchromium\t\xff\xfe\tunexpected\tfailed\n")
    cand = write_tsv(tmp_path / "cand.tsv", [row()])
    assert_loud(["--base", str(base), "--candidate", str(cand)])
    assert "读不到 baseline 侧 TSV" in capsys.readouterr().err


def test_unexpected_crash_maps_to_rc_2(monkeypatch):
    """rc=1 必须只表示「对比完成且存在形态差异」：任何崩溃都要归到 rc=2（不可豁免）。"""

    def boom(argv):
        raise RuntimeError("模拟脚本自身崩溃")

    monkeypatch.setattr(e2e_morph_compare, "_run", boom)
    assert e2e_morph_compare.main(["--base", "x", "--candidate", "y"]) == 2


def test_crash_handler_survives_broken_stderr(monkeypatch):
    """诊断输出自身失败（管道关闭/编码）也不能让异常逃逸成默认 rc=1。"""

    class BrokenStderr:
        def write(self, *args):
            raise OSError("stderr 已关闭")

        def flush(self):
            raise OSError("stderr 已关闭")

    def boom(argv):
        raise RuntimeError("模拟崩溃")

    monkeypatch.setattr(e2e_morph_compare, "_run", boom)
    monkeypatch.setattr(e2e_morph_compare.sys, "stderr", BrokenStderr())
    assert e2e_morph_compare.main([]) == 2


def test_status_sentinel_is_rejected_but_result_sentinel_tolerated(tmp_path):
    """第 4 列 "?"（reporter 形态漂移）语义不明 -> 拒止；第 5 列 "?" 只影响披露 -> 容忍。"""
    base = write_tsv(tmp_path / "base.tsv", [row(status="?", result="passed")])
    cand = write_tsv(tmp_path / "cand.tsv", [row()])
    assert_loud(["--base", str(base), "--candidate", str(cand)])
    base = write_tsv(tmp_path / "base.tsv", [row(result="?")])
    assert e2e_morph_compare.main(["--base", str(base), "--candidate", str(cand)]) == 0


def test_title_with_unicode_line_separator_is_not_torn(tmp_path):
    """splitlines() 会在 \\u2028 处断行；只按 \\n 切行才不会把标题撕成两行误报列数不符。"""
    base = write_tsv(tmp_path / "base.tsv", [row(title="A\u2028B")])
    cand = write_tsv(tmp_path / "cand.tsv", [row(title="A\u2028B")])
    assert e2e_morph_compare.main(["--base", str(base), "--candidate", str(cand)]) == 0


def test_crlf_is_tolerated(tmp_path):
    base = tmp_path / "base.tsv"
    base.write_bytes("\t".join(row()).encode() + b"\r\n")
    cand = write_tsv(tmp_path / "cand.tsv", [row()])
    assert e2e_morph_compare.main(["--base", str(base), "--candidate", str(cand)]) == 0


def test_run_row_description_change_is_visible(tmp_path):
    """形态键含 skip 文案（非 skip 行也一样）：诊断必须显示文案差异，不能渲染成两个 run。"""
    rc, summary = run(
        tmp_path,
        [row(status="expected", skip_desc="注解A")],
        [row(status="expected", skip_desc="注解B")],
    )
    assert rc == 1
    assert "run:注解A" in summary and "run:注解B" in summary


def test_full_disclosure_reaches_stdout_while_summary_is_capped(tmp_path, capsys):
    """Summary 超 LIST_CAP 会写「见 job 日志」：日志里必须真有全量，不能指向空处。"""
    n = e2e_morph_compare.LIST_CAP + 10
    base_rows = [row(title=f"用例{i:03d}") for i in range(n)]
    cand_rows = [row(title=f"用例{i:03d}", status="unexpected", result="failed")
                 for i in range(n)]
    rc, summary = run(tmp_path, base_rows, cand_rows)
    assert rc == 0
    assert f"另有 {n - e2e_morph_compare.LIST_CAP} 条，见 job 日志" in summary
    out = capsys.readouterr().out
    assert "用例000" in out and f"用例{n - 1:03d}" in out, "stdout 必须给出全量（不截断）"
