# scripts/e2e_normalize.py 的单测（issue #410）。
# 全部用合成 JSON fixture——A 层可信度 100% 依赖「Playwright JSON reporter 形态不变」这条
# 外部假设，#402 已证明它会变（1.62 把 title 挪到 spec 节点自身）。这里的 fixture 就是
# 那条假设的登记处：Playwright 升级后先跑这个文件，比人肉实跑一整轮便宜。
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import e2e_normalize


def entry(project, status, result, anns=None, result_anns=None):
    # 注意：tests[] 元素刻意不放 title 键——#402 形态（title 只在 spec 节点自身），
    # 旧实现 t["title"] 在这种形态下必 KeyError。
    return {
        "projectName": project,
        "status": status,
        "expectedStatus": "passed",
        "timeout": 30000,
        "annotations": anns or [],
        "results": [{"status": result, "duration": 1, "annotations": result_anns or []}],
    }


def spec_node(title, file, tests):
    node = {"title": title, "ok": True, "id": "x", "tags": [], "tests": tests,
            "line": 1, "column": 1}
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


def run(tmp_path, data, projects="chromium,mobile"):
    raw = tmp_path / "raw.json"
    out = tmp_path / "out.tsv"
    raw.write_text(json.dumps(data))
    e2e_normalize.main(["--input", str(raw), "--output", str(out),
                        "--projects", projects, "--side", "baseline"])
    return out.read_text().splitlines()


def test_happy_path_nested_suites(tmp_path):
    data = report([file_suite([
        spec_node("用例B", "e2e/demo.spec.ts", [
            entry("chromium", "expected", "passed"),
            entry("mobile", "skipped", "skipped",
                       result_anns=[{"type": "skip", "description": "镜像"}]),
        ]),
        spec_node("用例A", "e2e/demo.spec.ts", [
            entry("chromium", "unexpected", "failed"),
            entry("mobile", "expected", "passed"),
        ]),
    ])], stats(expected=2, unexpected=1, skipped=1))
    lines = run(tmp_path, data)
    assert lines == [
        "demo\tchromium\t用例A\tunexpected\tfailed\t",
        "demo\tchromium\t用例B\texpected\tpassed\t",
        "demo\tmobile\t用例A\texpected\tpassed\t",
        "demo\tmobile\t用例B\tskipped\tskipped\t镜像",
    ]


def test_stats_mismatch_rejected(tmp_path):
    data = report([file_suite([
        spec_node("用例A", "e2e/demo.spec.ts", [entry("chromium", "expected", "passed")]),
    ])], stats(expected=2))  # 实际只有 1 行
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, data)
    assert "stats" in str(exc.value)


def test_missing_project_rejected(tmp_path):
    data = report([file_suite([
        spec_node("用例A", "e2e/demo.spec.ts", [
            entry("chromium", "expected", "passed"),
            entry("chromiun", "expected", "passed"),  # 拼错的 project 名
        ]),
    ])], stats(expected=2))
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, data)
    assert "mobile" in str(exc.value)


def test_setup_project_filtered_after_stats_guard(tmp_path):
    setup_suite = {"title": "e2e/auth.setup.ts", "file": "e2e/auth.setup.ts",
                   "line": 1, "column": 1,
                   "specs": [spec_node("登录", "e2e/auth.setup.ts",
                                       [entry("setup", "expected", "passed")])]}
    data = report([setup_suite, file_suite([
        spec_node("用例A", "e2e/demo.spec.ts", [
            entry("chromium", "expected", "passed"),
            entry("mobile", "expected", "passed"),
        ]),
    ])], stats(expected=3))  # stats 含 setup 行
    lines = run(tmp_path, data)
    assert len(lines) == 2
    assert all("登录" not in line for line in lines)


def test_skip_desc_from_test_level_annotations(tmp_path):
    data = report([file_suite([
        spec_node("用例A", "e2e/demo.spec.ts", [
            entry("chromium", "skipped", "skipped",
                       anns=[{"type": "skip", "description": "平台数不足"}]),
            entry("mobile", "expected", "passed"),
        ]),
    ])], stats(expected=1, skipped=1))
    lines = run(tmp_path, data)
    assert lines[0].endswith("\t平台数不足")


def test_spec_label_fallback_to_ancestor_suite_file(tmp_path):
    spec = spec_node("用例A", None, [  # spec 节点无 file → 回落祖先 suite 的 file
        entry("chromium", "expected", "passed"),
        entry("mobile", "expected", "passed"),
    ])
    data = report([file_suite([spec])], stats(expected=2))
    lines = run(tmp_path, data)
    assert all(line.startswith("demo\t") for line in lines)


def test_spec_label_missing_everywhere_is_loud(tmp_path):
    spec = spec_node("用例A", None, [
        entry("chromium", "expected", "passed"),
        entry("mobile", "expected", "passed"),
    ])
    suite = file_suite([spec])
    del suite["file"]  # 祖先 suite 也没有 file
    data = report([suite], stats(expected=2))
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, data)
    assert "file" in str(exc.value)


def test_invalid_json_is_loud(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text("not json at all")
    with pytest.raises(SystemExit) as exc:
        e2e_normalize.main(["--input", str(raw), "--output", str(tmp_path / "o.tsv"),
                            "--projects", "chromium,mobile", "--side", "candidate"])
    assert "不是合法 JSON" in str(exc.value)
    assert "candidate" in str(exc.value)
