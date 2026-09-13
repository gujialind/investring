# ============================================================================
# 单元测试守门：MySQL 门控用例必须带 dialect 标记（issue #469 / PR #474 评审）
# ============================================================================
# 背景：`dialect` 标记表达「本用例只在 MySQL 方言下有效（SQLite 下自动 skip）」。
# 漏标不会让 CI 立刻变红（当前未按 marker 过滤），但一旦将来按
# `-m "integration or dialect"` 收窄 MySQL job，漏标用例会静默退出 MySQL 验证——
# 正是「覆盖无声蒸发」，且没有任何机器门禁会发现。PR #474 评审实测：SQLite 全量
# 30 条 skip 只有 23 条带标，test_migration_0015.py 的 7 条漏网（「grep 逐个确认」
# 没有机器保障，#382 教训）。本文件把该判据固化为守门。
#
# 判据（刻意保守，只查用例自身直接可见的门控，宁少报不误报）：
# - 扫描 tests/**/*.py 里的 test 函数（含类内方法）；
# - 用例自身出现 `_mysql_only()` 调用，或同时出现 `dialect.name` 访问与含 "mysql"
#   的字符串常量（即「MySQL 方言判断」）→ 视为门控用例，必须带 dialect 标记
#   （函数装饰器 / 所在类装饰器 / 模块级 pytestmark 三者之一）；
# - 反向用例（`dialect.name != "sqlite"` 这类只在 SQLite 有效的）不在此列；
# - 已知盲区：经自写中间 helper 间接门控的用例查不到（如 test 调用 helper、helper
#   再判方言）。新增此类写法时请显式给用例打 dialect 标记。
# ============================================================================

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]  # backend/tests


def _bears_dialect_mark(node) -> bool:
    """装饰器列表里是否含 pytest.mark.dialect（兼容带参形式）。"""
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "dialect":
            return True
    return False


def _module_pytestmark_has_dialect(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            if "dialect" in ast.dump(node.value):
                return True
    return False


def _is_mysql_gated(fn) -> bool:
    """用例自身是否直接门控 MySQL：调用 `_mysql_only()`，或判断 dialect.name 含 "mysql"。"""
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name) and f.id == "_mysql_only":
                return True
            if isinstance(f, ast.Attribute) and f.attr == "_mysql_only":
                return True
        if isinstance(sub, ast.Attribute) and sub.attr == "name":
            owner = sub.value
            if isinstance(owner, ast.Attribute) and owner.attr == "dialect":
                if any(
                    isinstance(c, ast.Constant)
                    and isinstance(c.value, str)
                    and "mysql" in c.value
                    for c in ast.walk(fn)
                ):
                    return True
    return False


def _collect_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_marked = _module_pytestmark_has_dialect(tree)

        def check(fn, class_node) -> None:
            if not fn.name.startswith("test_"):
                return
            marked = (
                module_marked
                or _bears_dialect_mark(fn)
                or (class_node is not None and _bears_dialect_mark(class_node))
            )
            if not marked and _is_mysql_gated(fn):
                owner = class_node.name if class_node is not None else "<module>"
                violations.append(f"{path.relative_to(TESTS_ROOT.parent)}:{fn.lineno} {owner}::{fn.name}")

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                check(node, None)
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        check(sub, node)
    return violations


def test_mysql_gated_tests_carry_dialect_mark():
    violations = _collect_violations()
    assert not violations, (
        "以下用例经 `_mysql_only()` / `dialect.name` 门控 MySQL，却没有 dialect 标记——"
        "将来按 `-m` 收窄 MySQL job 时它们会静默退出验证，请补 `@pytest.mark.dialect`"
        "（函数、所在类或模块 pytestmark 均可）：\n  " + "\n  ".join(violations)
    )
