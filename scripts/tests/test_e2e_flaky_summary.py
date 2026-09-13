# scripts/e2e_flaky_summary.py 的单测（issue #466）。
# 与 test_e2e_normalize.py 同构：合成 JSON fixture 是「Playwright JSON reporter 形态
# 不变」这条外部假设的登记处，Playwright 升级后先跑这里（比人肉实跑一整轮便宜）。
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import e2e_flaky_summary


def entry(project, status, result):
    # tests[] 元素刻意不放 title 键——#402 形态（title 只在 spec 节点自身）
    return {
        "projectName": project,
        "status": status,
        "expectedStatus": "passed",
        "timeout": 30000,
        "annotations": [],
        "results": [{"status": result, "duration": 1, "annotations": []}],
    }


def spec_node(title, file, tests):
    node = {"title": title, "ok": True, "id": "x", "tags": [], "tests": tests,
            "line": 42, "column": 1}
    if file is not None:
        node["file"] = file
    return node


def file_suite(specs, file="e2e/demo.spec.ts"):
    return {"title": file, "file": file, "line": 1, "column": 1,
            "suites": [{"title": "分组", "specs": specs}]}


def report(suites, stats):
    return {"config": {}, "suites": suites, "stats": stats}


def stats(expected=0, unexpected=0, flaky=0, skipped=0):
    return {"expected": expected, "unexpected": unexpected, "flaky": flaky,
            "skipped": skipped, "startTime": "t", "duration": 1}


def run(tmp_path, data, capsys):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(data))
    e2e_flaky_summary.main(["--input", str(raw)])
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_no_flaky_is_quiet(tmp_path, capsys):
    data = report(
        [file_suite([
            spec_node("用例 A", None, [entry("chromium", "expected", "passed")]),
            spec_node("用例 B", None, [entry("mobile", "expected", "passed")]),
        ])],
        stats(expected=2),
    )
    out, err = run(tmp_path, data, capsys)
    assert "无 flaky 用例" in out
    assert "2 条用例记录" in out
    assert err == "", "无 flaky 时不得发 warning"


def test_flaky_listed_and_warned(tmp_path, capsys):
    data = report(
        [file_suite([
            spec_node("稳定用例", None, [entry("chromium", "expected", "passed")]),
            spec_node("爱抖的用例", None, [entry("mobile", "flaky", "passed")]),
        ])],
        stats(expected=1, flaky=1),
    )
    out, err = run(tmp_path, data, capsys)
    assert "1 个用例 flaky" in out
    assert "| mobile | 爱抖的用例 | `e2e/demo.spec.ts:42` |" in out
    assert "::warning::1 个 E2E 用例 flaky" in err


def test_stats_mismatch_fails_loud(tmp_path, capsys):
    # stats 自报 0 flaky 但节点里有 flaky → reporter 形态已变，必须响亮失败
    data = report(
        [file_suite([
            spec_node("爱抖的用例", None, [entry("chromium", "flaky", "passed")]),
        ])],
        stats(expected=1),
    )
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, data, capsys)
    assert exc.value.code != 0
    assert "JSON reporter 形态可能已变" in str(exc.value)


def test_missing_input_is_not_fatal(tmp_path, capsys):
    # playwright 没跑到产出阶段（E2E job 自己已经红了）：提示即可，不额外染红
    e2e_flaky_summary.main(["--input", str(tmp_path / "absent.json")])
    captured = capsys.readouterr()
    assert "未找到" in captured.out
    assert captured.err == ""


def test_total_mismatch_fails_loud(tmp_path, capsys):
    # 逐节点只收到 1 条，stats 却报 5 条 → 与 normalizer 同口径的形态守卫，必须响亮失败
    # （只比 flaky 数会漏掉这类「节点整片识别不到」的形态变化）
    data = report(
        [file_suite([
            spec_node("普通用例", None, [entry("chromium", "expected", "passed")]),
        ])],
        stats(expected=5),
    )
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, data, capsys)
    assert exc.value.code != 0
    assert "reporter stats 总数" in str(exc.value)


def test_zero_records_fails_loud(tmp_path, capsys):
    # reporter 产出 0 条用例记录 = 运行/过滤配置坏了（--grep/--project 全滤掉或形态变化）；
    # job 可能仍绿，必须响亮失败而不是打印「无 flaky」放过
    data = report([], stats())
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, data, capsys)
    assert exc.value.code != 0
    assert "0 条用例记录" in str(exc.value)


def test_file_falls_back_to_ancestor_suite(tmp_path, capsys):
    # spec 节点自身缺 file 时回落祖先 suite 的 file（与 normalizer 同口径）
    data = report(
        [file_suite([
            spec_node("无 file 字段的用例", None, [entry("chromium", "flaky", "passed")]),
        ], file="e2e/fallback.spec.ts")],
        stats(flaky=1),
    )
    out, _ = run(tmp_path, data, capsys)
    assert "`e2e/fallback.spec.ts:42`" in out
