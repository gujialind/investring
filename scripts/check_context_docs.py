"""Check the bounded AI documentation entry points, not all repository Markdown."""

import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
MANAGED_DOCS = (
    "AGENTS.md",
    "CLAUDE.md",
    "backend/AGENTS.md",
    "frontend/AGENTS.md",
    "ir-cli/AGENTS.md",
    "docs/reference/documentation.md",
    "docs/reference/business-constraints.md",
    "docs/reference/logging.md",
    "docs/reference/code-review.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/runbooks/493-model-cutover.md",
)
SOURCE_REFERENCES = (
    "backend/app/services/snapshot_service.py",
    "backend/app/services/trade_service.py",
    "backend/tests/unit/test_error_codes_doc_sync.py",
    "backend/tests/seed_base.py",
    "backend/tests/integration/test_audit_log.py",
    "frontend/src/lib/tradePairs.ts",
    "frontend/src/lib/tradePairs.test.ts",
    "frontend/e2e/datepicker-in-dialog.spec.ts",
)
BUSINESS_DOC = "docs/reference/business-constraints.md"
REQUIRED_REFERENCES = {
    "AGENTS.md": {
        "backend/AGENTS.md", "frontend/AGENTS.md", "ir-cli/AGENTS.md",
        "ir-cli/CLI_MANUAL.md", "docs/reference/documentation.md",
        "docs/reference/logging.md", "docs/reference/code-review.md",
        "docs/reference/versioning.md", "docs/design/visual-spec.md",
        *(f"{BUSINESS_DOC}#rule-{topic}" for topic in (
            "ledger", "portfolio", "investor", "product", "cash", "snapshot",
            "lifecycle", "subscription", "trade", "event", "precision", "trading-day",
        )),
    },
    "CLAUDE.md": {"AGENTS.md"},
    "backend/AGENTS.md": {BUSINESS_DOC, "docs/reference/documentation.md",
                           "docs/reference/logging.md#logging-audit"},
    "frontend/AGENTS.md": {BUSINESS_DOC, "docs/reference/documentation.md",
                            "docs/design/visual-spec.md"},
    "ir-cli/AGENTS.md": {BUSINESS_DOC, "docs/reference/documentation.md",
                          "ir-cli/CLI_MANUAL.md"},
}
LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>\n]+>|[^\s()]+)(?:\s+\"[^\"\n]*\")?\)")
REFERENCE = re.compile(r"^\s*\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)", re.MULTILINE)
INCLUDE = re.compile(r"^@(AGENTS\.md)\s*$", re.MULTILINE)
EXPLICIT_ID = re.compile(r"<a\s+id=[\"']([^\"']+)[\"']\s*></a>")
LEGACY_ROOT = re.compile(
    r"(?:根(?:指南|文档)?\s*(?:`?AGENTS\.md`?)?|(?<![\w/])`?AGENTS\.md`?)"
    r"\s*(?:§\s*2(?:\.\d+)*|[「\"](?:核心领域模型|平台与现金账本|快照)[」\"])"
)


def without_fences(text: str) -> str:
    lines = []
    fence = ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker and not fence:
            fence = marker[1]
            lines.append("\n")
        elif fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            lines.append("\n")
        else:
            lines.append(line)
    return "".join(lines)


def anchors(text: str) -> set[str]:
    text = without_fences(text)
    result = set(EXPLICIT_ID.findall(text))
    counts = Counter()
    for heading in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        title = re.sub(r"<[^>]+>", "", heading[1])
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        index = counts[slug]
        counts[slug] += 1
        result.add(f"{slug}-{index}" if index else slug)
    return result


def check(root: Path = REPO_ROOT) -> list[str]:
    root = root.resolve()
    errors = []
    texts = {}
    rule_locations = {}
    target_anchors = {}
    target_explicit_ids = {}
    for name in (*MANAGED_DOCS, *SOURCE_REFERENCES):
        path = root / name
        if not path.is_file():
            errors.append(f"{name}:1: missing managed file")
            continue
        text = path.read_text(encoding="utf-8")
        texts[name] = without_fences(text) if name in MANAGED_DOCS else text

    for name, text in texts.items():
        def report(offset: int, reason: str) -> None:
            line = text.count("\n", 0, offset) + 1
            errors.append(f"{name}:{line}: {reason}")

        for match in LEGACY_ROOT.finditer(text):
            report(match.start(), f"legacy root domain reference: {match[0]}")
        if name in MANAGED_DOCS:
            for match in EXPLICIT_ID.finditer(text):
                anchor = match[1]
                if anchor.startswith("rule-"):
                    if anchor in rule_locations:
                        report(match.start(), f"duplicate ID {anchor}; first in {rule_locations[anchor]}")
                    else:
                        rule_locations[anchor] = name

        found = set()
        patterns = (LINK, REFERENCE, INCLUDE) if name == "CLAUDE.md" else (LINK, REFERENCE)
        for pattern in patterns:
            for match in pattern.finditer(text):
                raw = match[1].strip("<>")
                url = urlsplit(raw)
                if url.scheme in {"http", "https", "mailto"} or (not url.scheme and url.netloc):
                    continue
                if url.scheme or url.path.startswith("/"):
                    report(match.start(), f"use a repository-relative link: {raw}")
                    continue
                target = ((root / name).parent / unquote(url.path)).resolve() if url.path else root / name
                if not target.is_relative_to(root):
                    report(match.start(), f"link escapes repository: {raw}")
                    continue
                relative = target.relative_to(root).as_posix()
                fragment = unquote(url.fragment)
                found.add(relative + (f"#{fragment}" if fragment else ""))
                if not target.exists():
                    report(match.start(), f"missing link target: {raw}")
                    continue
                if fragment:
                    if target.suffix != ".md":
                        report(match.start(), f"use a stable symbol beside the source link: {raw}")
                        continue
                    if target not in target_anchors:
                        target_text = target.read_text(encoding="utf-8")
                        target_anchors[target] = anchors(target_text)
                        target_explicit_ids[target] = set(EXPLICIT_ID.findall(without_fences(target_text)))
                    if fragment not in target_anchors[target]:
                        report(match.start(), f"missing anchor: {raw}")
                    elif fragment.startswith("rule-") and fragment not in target_explicit_ids[target]:
                        report(match.start(), f"missing explicit rule ID: {raw}")
        for required in sorted(REQUIRED_REFERENCES.get(name, set()) - found):
            errors.append(f"{name}:1: missing required reference: {required}")
    return errors


def main(root: Path = REPO_ROOT) -> int:
    errors = check(root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Context docs OK ({len(MANAGED_DOCS)} documents, {len(SOURCE_REFERENCES)} source-reference files; no recursive or external checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
