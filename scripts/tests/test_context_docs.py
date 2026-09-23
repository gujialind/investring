import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_context_docs.py"
SPEC = importlib.util.spec_from_file_location("check_context_docs", SCRIPT)
docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs)


@pytest.fixture
def doc_tree(tmp_path):
    contents = {name: "# Guide\n" for name in (*docs.MANAGED_DOCS, *docs.SOURCE_REFERENCES)}
    for name, references in docs.REQUIRED_REFERENCES.items():
        for reference in sorted(references):
            target, _, anchor = reference.partition("#")
            contents.setdefault(target, "# Target\n")
            if anchor:
                marker = f'<a id="{anchor}"></a>\n'
                if marker not in contents[target]:
                    contents[target] += marker
            relative = os.path.relpath(tmp_path / target, (tmp_path / name).parent)
            if name == "CLAUDE.md":
                contents[name] += f"@{relative}\n"
            else:
                contents[name] += f"[reference]({relative}{'#' + anchor if anchor else ''})\n"
    for name, text in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _append(path, text):
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_valid_managed_tree(doc_tree):
    assert docs.check(doc_tree) == []


def test_repository_links_and_entries():
    assert docs.check() == []


def test_missing_managed_file(doc_tree):
    (doc_tree / "CLAUDE.md").unlink()
    assert "CLAUDE.md:1: missing managed file" in docs.check(doc_tree)


def test_missing_link_target_reports_source_line(doc_tree):
    path = doc_tree / "docs/reference/documentation.md"
    path.write_text("# Docs\n[missing](absent.md)\n", encoding="utf-8")
    assert "docs/reference/documentation.md:2: missing link target: absent.md" in docs.check(doc_tree)


def test_missing_rule_anchor(doc_tree):
    path = doc_tree / docs.BUSINESS_DOC
    path.write_text(path.read_text().replace('<a id="rule-cash"></a>', ""), encoding="utf-8")
    assert any("missing anchor:" in e and "#rule-cash" in e for e in docs.check(doc_tree))


def test_heading_does_not_replace_explicit_rule_id(doc_tree):
    path = doc_tree / docs.BUSINESS_DOC
    path.write_text(path.read_text().replace('<a id="rule-cash"></a>', '## rule-cash'), encoding="utf-8")
    assert any("missing explicit rule ID:" in e and "#rule-cash" in e for e in docs.check(doc_tree))


@pytest.mark.parametrize("name", [docs.BUSINESS_DOC, "docs/reference/logging.md"])
def test_duplicate_rule_id_in_one_or_two_documents(doc_tree, name):
    path = doc_tree / name
    path.write_text(path.read_text() + '<a id="rule-cash"></a>\n', encoding="utf-8")
    assert any("duplicate ID rule-cash" in e for e in docs.check(doc_tree))


def test_missing_required_navigation_reference(doc_tree):
    path = doc_tree / "AGENTS.md"
    path.write_text(path.read_text().replace("[reference](backend/AGENTS.md)\n", ""), encoding="utf-8")
    assert "AGENTS.md:1: missing required reference: backend/AGENTS.md" in docs.check(doc_tree)


@pytest.mark.parametrize("reference", [
    "根 `AGENTS.md` §2.11", "根 §2.5/§2.3", "根 AGENTS.md「快照」节",
])
def test_legacy_root_reference_in_source(doc_tree, reference):
    path = doc_tree / "frontend/src/lib/tradePairs.ts"
    path.write_text(f"// {reference}\n", encoding="utf-8")
    assert any(e.startswith("frontend/src/lib/tradePairs.ts:1: legacy root") for e in docs.check(doc_tree))


def test_module_sections_and_fenced_examples_are_not_legacy_root(doc_tree):
    _append(doc_tree / "backend/AGENTS.md", "## 2. 跑测试\n")  # 让首行的跨文件 §2 有真实落点
    path = doc_tree / "docs/reference/documentation.md"
    path.write_text("backend/AGENTS.md §2\n```text\n根 AGENTS.md §2.5\n[x](missing.md)\n```\n", encoding="utf-8")
    assert docs.check(doc_tree) == []


def test_section_reference_resolves_inside_the_same_document(doc_tree):
    _append(doc_tree / "docs/reference/documentation.md", "## 2. 条目\n见 §2 与本节。\n")
    assert docs.check(doc_tree) == []


def test_renumbering_a_heading_turns_its_section_reference_red(doc_tree):
    path = doc_tree / "docs/reference/documentation.md"
    _append(path, "## 2. 条目\n见 §2。\n")
    path.write_text(path.read_text(encoding="utf-8").replace("## 2. 条目", "## 3. 条目"), encoding="utf-8")
    assert ("docs/reference/documentation.md:3: missing section: §2 in docs/reference/documentation.md"
            in docs.check(doc_tree))


def test_missing_section_number_reports_its_line(doc_tree):
    _append(doc_tree / "docs/reference/documentation.md", "见 §9.9 的细节。\n")
    assert ("docs/reference/documentation.md:2: missing section: §9.9 in docs/reference/documentation.md"
            in docs.check(doc_tree))


def test_cross_file_section_reference_resolves_in_the_named_document(doc_tree):
    _append(doc_tree / "docs/design/visual-spec.md", "## 1.5 豁免登记\n")
    _append(doc_tree / "docs/reference/logging.md", "豁免见 `docs/design/visual-spec.md` §1.5。\n")
    assert docs.check(doc_tree) == []


def test_cross_file_section_reference_reports_the_named_document(doc_tree):
    _append(doc_tree / "docs/design/visual-spec.md", "## 1.5 豁免登记\n")
    _append(doc_tree / "docs/reference/logging.md", "豁免见 `docs/design/visual-spec.md` §9.9。\n")
    assert any("missing section: §9.9 in docs/design/visual-spec.md" in e for e in docs.check(doc_tree))


def test_root_agents_section_reference_resolves_from_a_nested_document(doc_tree):
    _append(doc_tree / "AGENTS.md", "### 3.5 AI AGENT铁律\n")
    _append(doc_tree / "docs/reference/code-review.md", "权限见根 `AGENTS.md` §3.5。\n")
    assert docs.check(doc_tree) == []


def test_missing_section_target_file_is_reported(doc_tree):
    _append(doc_tree / "docs/reference/documentation.md", "见 `absent.md` §9.9。\n")
    assert any("missing section target: absent.md" in e for e in docs.check(doc_tree))


def test_section_number_in_a_heading_is_not_a_reference(doc_tree):
    _append(doc_tree / "docs/reference/documentation.md", "### 6.1 §9.9 处置留痕\n")
    assert docs.check(doc_tree) == []


@pytest.mark.parametrize("reference", ["#237（§9.9 已过时）\n", "原有 §9.9 的口径\n", "曾在 §9.9 写过\n"])
def test_historical_section_references_are_exempt(doc_tree, reference):
    _append(doc_tree / "docs/reference/documentation.md", reference)
    assert docs.check(doc_tree) == []


def test_section_reference_inside_a_fence_is_not_checked(doc_tree):
    _append(doc_tree / "docs/reference/documentation.md", "```text\n见 §9.9\n```\n")
    assert docs.check(doc_tree) == []


def test_section_references_outside_managed_docs_are_not_checked(doc_tree):
    _append(doc_tree / "docs/design/visual-spec.md", "见 §9.9 的说明。\n")
    assert docs.check(doc_tree) == []


def test_reference_definition_and_heading_anchor(doc_tree):
    path = doc_tree / "docs/reference/documentation.md"
    path.write_text("# 标题\n[here]: #标题\n[missing]: #不存在\n", encoding="utf-8")
    assert docs.check(doc_tree) == ["docs/reference/documentation.md:3: missing anchor: #不存在"]


def test_unmanaged_files_and_external_sites_are_not_read(doc_tree):
    (doc_tree / "history.md").write_text("[broken](absent.md)", encoding="utf-8")
    path = doc_tree / "docs/reference/documentation.md"
    path.write_text("[external](https://example.invalid/missing#anchor)\n", encoding="utf-8")
    assert docs.check(doc_tree) == []


@pytest.mark.parametrize("local_link", ["file:///tmp/guide.md", "file://localhost/tmp/guide.md", "/tmp/guide.md"])
def test_absolute_and_outside_links_are_rejected(doc_tree, local_link):
    path = doc_tree / "docs/reference/documentation.md"
    path.write_text(f"[local]({local_link})\n[out](../../../outside.md)\n", encoding="utf-8")
    errors = docs.check(doc_tree)
    assert any("repository-relative" in e for e in errors)
    assert any("link escapes repository" in e for e in errors)


def test_cli_failure_is_nonzero_from_another_cwd(doc_tree, tmp_path):
    script = doc_tree / "scripts/check_context_docs.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    (doc_tree / "CLAUDE.md").write_text("# Missing entry\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path.parent,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "CLAUDE.md:1: missing required reference: AGENTS.md" in result.stderr
