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
