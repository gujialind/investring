# ============================================================================
# 单元测试：错误码 ↔ 文档一致性守门 (test_error_codes_doc_sync.py)
# ============================================================================
# issue #418：[业务规则](../../../docs/reference/business-constraints.md#错误码总表) 是错误码事实来源，但全仓
# **没有**错误码注册表（`app/services/exceptions.py` 只有载体类，`code` 是自由 str），
# 于是漂移不会让任何测试变红——实测 81 个在用码只记了 26 个。
#
# 本测试把该文档的「错误码总表」与代码在用码集合绑死（做法类比 test_audit_service.py
# 的列宽守门、scripts/check_openapi.py 的契约守门）：
#   A. 在用码 == 总表首列 → 缺项与死码名都红；
#   B. 正文（总表以外）反引号引用的码必须都在用 → 防「码名写进散文里就算记过」；
#   C. 总表「抛出位置」锚点必须是稳定符号（`file::函数/类名`）且逐一可验证：
#      符号存在 + 该符号行范围内确实抛出该码；行号锚点一律判红（#521——行位移
#      会让 `file:NNN` 锚点静默失真，#493 一次重写即波及数十处）。
#
#   known_gap（#583 决策 ①，2026-09-20 由所有者选定）：**不校验「代码里的抛出点是否
#     都进了表」**——A 只比码的集合、C 是文档→代码方向，三项里没有一项断言「每个抛出
#     符号都出现在该码的锚点列」，于是「已有码新增抛出点」这一类漂移静默通过。实例：
#     #493 给 `NON_TRADING_DAY` / `INVALID_DATE_ORDER` 各新增 3 处抛出点，守门全绿，
#     直到 #517 的评估才被人工发现（已随 #583 手工补录）。刻意不升级为双向：一次性要
#     补齐全仓历史缺口（不止本轮 6 处，例如 `NON_TRADING_DAY` 的申赎与转移抛出点也未
#     进锚点列），且函数级抛出点会随重构频繁触发返修——成本由整个历史存量承担。
#     该类漂移改由代码审查兜底（见 docs/reference/code-review.md）。
#
# 提取口径（AST 而非 grep，覆盖全部抛出形态）：
#   1. `BusinessError` / `NotFoundError` 及其子类（按 ClassDef 基类推导）的 `code`
#      ——位置参数与 `code=` 关键字；`BusinessError(*_TUPLE_CONST)` 的 splat 解析到
#      模块级 tuple 常量的首元素（如 `routers/trading_calendar.py` 的
#      `_CALENDAR_NOT_SYNCED` 常量，抛出点仍记在 `raise` 所在函数名下）；
#      子类把码定在自己 `__init__` 的 `super().__init__("CODE", ...)` 里的形态从类体取
#      （`NavNotAvailableError` → NAV_NOT_AVAILABLE、`InvalidStatusError` → INVALID_STATUS）；
#   2. 任意 dict 字面量里键为 `"error"`（router inline `detail={"error": ...}`）或
#      `"code"`（结构化错误条目）的大写字符串常量，以及同义的下标赋值
#      `entry["code"] = "CODE"`。
# 全部码为字面量、无 f-string 拼接，故静态守门可行。非错误码的同形常量天然被排除：
# `init_tasks.py` 的任务码是小写，`details={"code": code}` 的值不是常量。
# ============================================================================

import ast
import re
from pathlib import Path
from typing import Dict, Set

BACKEND_APP = Path(__file__).resolve().parents[2] / "app"
CONSTRAINTS_DOC = (
    Path(__file__).resolve().parents[3] / "docs" / "reference" / "business-constraints.md"
)
TABLE_HEADING = "## 错误码总表"

# 码字面量：全大写常量名（含 FORBIDDEN 这类无下划线的单词形）
CODE_LITERAL = re.compile(r"[A-Z][A-Z0-9_]*")
# 正文引用：额外要求至少一段下划线，排除散文里反引号包裹的 `N` 这类单字母
PROSE_CODE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
BASE_ERROR_CLASSES = {"BusinessError", "NotFoundError"}
# 总表「抛出位置」锚点（#521）：只认稳定符号形态，行号形态判红
ANCHOR_FULL = re.compile(r"^(?P<file>[\w/]+\.py)::(?P<sym>[\w.]+)$")
ANCHOR_CONT = re.compile(r"^::(?P<sym>[\w.]+)$")
ANCHOR_LINE = re.compile(r"^(?:[\w/]+\.py)?:\d+$")


def _is_code_literal(value: str) -> bool:
    return CODE_LITERAL.fullmatch(value) is not None


def _iter_py_files():
    return sorted(p for p in BACKEND_APP.rglob("*.py") if "__pycache__" not in p.parts)


def _error_class_names(tree: ast.AST) -> Set[str]:
    """收集本文件内的错误载体类名（含 BusinessError/NotFoundError 的子类）。"""
    names = set(BASE_ERROR_CLASSES)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name not in names:
                for base in node.bases:
                    base_name = (
                        base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                    )
                    if base_name in names:
                        names.add(node.name)
                        changed = True
    return names


def _is_super_init_call(node: ast.Call) -> bool:
    """`super().__init__(...)` 调用（错误载体子类把码定在其中的形态）。"""
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "__init__"
        and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name)
        and func.value.func.id == "super"
    )


def _module_code_tuples(tree: ast.Module) -> Dict[str, str]:
    """模块级 `_X = ("CODE", "message")` 常量 → {常量名: 码}（供 splat 抛出解析）。"""
    out: Dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Tuple):
            continue
        elements = node.value.elts
        if not elements:
            continue
        first = elements[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if not _is_code_literal(first.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = first.value
    return out


def _const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _emissions_from_tree(tree: ast.Module) -> list[tuple[str, int]]:
    """提取本文件全部「码字面量的抛出形态」，返回 [(码, 行号)]。

    提取口径与下文各部分一致，额外带行号——锚点守门（#521）要判断某码是否
    落在锚点符号的行范围内。
    """
    error_classes = _error_class_names(tree)
    tuples = _module_code_tuples(tree)
    emissions: list[tuple[str, int]] = []

    def add(code: str, lineno: int) -> None:
        emissions.append((code, lineno))

    # 载体子类把码定在自己的 `__init__`（`super().__init__("CODE", ...)`），
    # 抛出点看不到字面量，必须从类体里取
    for class_def in ast.walk(tree):
        if not isinstance(class_def, ast.ClassDef) or class_def.name not in error_classes:
            continue
        for node in ast.walk(class_def):
            if not isinstance(node, ast.Call) or not _is_super_init_call(node):
                continue
            for arg in node.args:
                literal = _const_str(arg)
                if literal is not None and _is_code_literal(literal):
                    add(literal, node.lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", None)
            )
            if func_name not in error_classes:
                continue
            for arg in node.args:
                literal = _const_str(arg)
                if literal is not None and _is_code_literal(literal):
                    add(literal, node.lineno)
                elif isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                    splat = tuples.get(arg.value.id)
                    if splat:
                        add(splat, node.lineno)
            for keyword in node.keywords:
                if keyword.arg != "code":
                    continue
                literal = _const_str(keyword.value)
                if literal is not None and _is_code_literal(literal):
                    add(literal, node.lineno)
        elif isinstance(node, ast.Assign):
            # 结构化错误条目的下标赋值形态：entry["code"] = "SESSION_ABORTED"
            literal = _const_str(node.value)
            if literal is None or not _is_code_literal(literal):
                continue
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _const_str(target.slice) == "code":
                    add(literal, node.lineno)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                key_literal = _const_str(key)
                if key_literal not in ("error", "code"):
                    continue
                literal = _const_str(value)
                if literal is not None and _is_code_literal(literal):
                    add(literal, node.lineno)
    return emissions


def _collect_from_file(path: Path) -> Set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {code for code, _ in _emissions_from_tree(tree)}


def in_use_codes() -> Set[str]:
    codes: Set[str] = set()
    for path in _iter_py_files():
        codes |= _collect_from_file(path)
    return codes


def _doc_text() -> str:
    return CONSTRAINTS_DOC.read_text(encoding="utf-8")


def _split_doc(text: str):
    """按总表标题切分文档：返回（总表正文，总表以外的正文）。"""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == TABLE_HEADING), None)
    assert start is not None, f"{CONSTRAINTS_DOC.name} 缺少「{TABLE_HEADING}」节"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end]), "\n".join(lines[:start] + lines[end:])


def table_codes() -> Set[str]:
    """总表首列（形如 | `CODE` | 触发条件 | …）里的码。"""
    section, _ = _split_doc(_doc_text())
    codes: Set[str] = set()
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        match = re.fullmatch(r"`([^`]+)`", cells[0])
        if match and _is_code_literal(match.group(1)):
            codes.add(match.group(1))
    return codes


def prose_codes() -> Set[str]:
    """总表以外正文里反引号包裹的码形 token。"""
    _, rest = _split_doc(_doc_text())
    return {
        match.group(1)
        for match in re.finditer(r"`([A-Z][A-Z0-9_]*)`", rest)
        if PROSE_CODE.fullmatch(match.group(1))
    }


def _anchors_from_section(section: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """解析总表正文里的「抛出位置」列 → ([(码, 文件, 符号)], [问题])。

    只认两种书写：`file.py::symbol`（首个 / 跨文件）与 `::symbol`（同文件续锚）。
    行号形态（`file.py:123`、`:123`）与其它无法解析的 token 一律记入问题列表。
    """
    anchors: list[tuple[str, str, str]] = []
    problems: list[str] = []
    for raw in section.splitlines():
        if not raw.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", raw.strip())[1:-1]]
        if not cells:
            continue
        match = re.fullmatch(r"`([^`]+)`", cells[0])
        if not (match and _is_code_literal(match.group(1))):
            continue
        code = match.group(1)
        if len(cells) < 4:
            # 整列缺失必须判红：静默跳过等于守门自己不守门（#521 评审）
            problems.append(f"`{code}`：总表行缺「抛出位置」列（锚点列为必填）")
            continue
        tokens = [t.strip() for t in cells[3].split(";") if t.strip()]
        if not tokens:
            problems.append(f"`{code}`：抛出位置列为空（#521 起必须给出稳定符号锚点）")
            continue
        current_file = None
        for token in tokens:
            full = ANCHOR_FULL.fullmatch(token)
            cont = ANCHOR_CONT.fullmatch(token)
            if full:
                current_file = full.group("file")
                anchors.append((code, current_file, full.group("sym")))
            elif cont:
                if current_file is None:
                    problems.append(f"`{code}`：续锚 `{token}` 之前没有文件锚点")
                else:
                    anchors.append((code, current_file, cont.group("sym")))
            elif ANCHOR_LINE.fullmatch(token):
                problems.append(f"`{code}`：行号锚点已禁用（#521），请改为稳定符号：{token}")
            else:
                problems.append(f"`{code}`：无法解析的锚点：{token!r}")
    return anchors, problems


def table_anchors() -> tuple[list[tuple[str, str, str]], list[str]]:
    """`_anchors_from_section` 作用于真实总表（供守门用例消费）。"""
    section, _ = _split_doc(_doc_text())
    return _anchors_from_section(section)


def _find_symbol_span(tree: ast.Module, dotted: str):
    """按点分路径定位符号（函数 / 类 / 嵌套方法），返回 (起行, 止行)。

    逐层下钻用 `ast.walk` 而非直接子节点：符号可能定义在 `for` / `if` 等复合
    语句体内（如 `snapshot_service.py` 的 `auto_confirm_after_snapshot._confirm_pair`），
    只找直接子节点会漏、把合法锚点误判为「符号不存在」。同名候选取源码顺序
    最靠前者，结果确定。
    """
    node: ast.AST = tree
    for part in dotted.split("."):
        candidates = [
            child
            for child in ast.walk(node)
            if child is not node
            and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and child.name == part
        ]
        if not candidates:
            return None
        node = min(candidates, key=lambda child: child.lineno)
    start = node.lineno
    decorators = [
        d.lineno for d in getattr(node, "decorator_list", []) if hasattr(d, "lineno")
    ]
    if decorators:
        start = min(decorators)
    return start, node.end_lineno


class TestErrorCodeDocSync:
    def test_extractor_covers_every_emission_shape(self):
        """提取口径自检：各抛出形态取一个已知码，防提取器静默退化为空集/漏形态"""
        codes = in_use_codes()
        assert codes, "未提取到任何在用错误码，检查提取器是否随抛出形态漂移"
        # service 层 BusinessError：位置参数 / code= 关键字
        assert "INSUFFICIENT_CASH" in codes
        assert "SNAPSHOT_NOT_CONTINUOUS" in codes
        # BusinessError 子类（InvalidStatusError / NavNotAvailableError）
        assert "INVALID_STATUS" in codes
        assert "NAV_NOT_AVAILABLE" in codes
        # router inline detail={"error": ...}（含无下划线的单词形）
        assert "RECALCULATION_FAILED" in codes
        assert "FORBIDDEN" in codes
        # 结构化错误条目 "code": ...
        assert "SESSION_ABORTED" in codes
        # 模块级 tuple 常量 splat
        assert "CALENDAR_NOT_SYNCED" in codes

    def test_doc_table_matches_in_use_codes(self):
        """验收：总表穷尽收录在用码——缺项与死码名都红"""
        codes = in_use_codes()
        documented = table_codes()
        missing = sorted(codes - documented)
        dead = sorted(documented - codes)
        assert not missing, f"代码在用但「{TABLE_HEADING}」未收录（{len(missing)} 个）：{missing}"
        assert not dead, f"总表记了但代码已不用的死码名（{len(dead)} 个）：{dead}"

    def test_prose_references_resolve_to_in_use_codes(self):
        """正文里反引号引用的码必须都在用，否则读者按散文查表会查空"""
        dangling = sorted(prose_codes() - in_use_codes())
        assert not dangling, (
            f"正文引用了代码中不存在的码：{dangling}"
            "（若确为非错误码的常量名，请去掉反引号或改写表述）"
        )


class TestErrorCodeAnchorGuard:
    """#521：总表「抛出位置」锚点必须是稳定符号，且逐一可验证抛出处。"""

    # 解析器自检样本：覆盖「首锚 / 同文件续锚 / 跨文件切换 / 嵌套符号」四种形态
    KNOWN_ANCHORS = {
        ("ACCOUNT_LOCKED", "dependencies.py", "get_current_user"),
        ("CASH_TRADE_FORBIDDEN", "services/trade_service.py", "create_trade"),
        (
            "CASH_TRANSFER_NON_CASH_LEG",
            "services/snapshot_service.py",
            "auto_confirm_after_snapshot._confirm_pair",
        ),
        ("INVALID_DATE_RANGE", "routers/share_change_events.py", "get_share_change_events"),
    }

    def test_anchor_parser_self_check(self):
        """锚点解析器自检：token 形态全合法（行号锚点判红）、锚点数非零"""
        anchors, problems = table_anchors()
        assert not problems, "总表锚点解析失败：\n" + "\n".join(problems)
        assert anchors, "未解析到任何锚点——解析器或总表格式已漂移"
        missing = sorted(self.KNOWN_ANCHORS - set(anchors))
        assert not missing, f"已知锚点未被解析出（解析器退化？）：{missing}"

    def test_anchor_rejection_paths(self):
        """判红口径自检：行号锚点 / 空列 / 裸续锚 / 无法解析 token 都必须留下问题

        用合成表正文而非真实总表——真实总表已无非法锚点，若只测真实文档，
        判红分支会在「看起来是死代码」的错觉里被后续重构删掉，守门静默失效。
        """
        section = "\n".join(
            [
                "| 码 | HTTP | 触发条件 | 抛出位置（取样） |",
                "| --- | --- | --- | --- |",
                "| `LINE_ANCHOR` | 422 | x | services/a.py:12; :34 |",
                "| `EMPTY_CELL` | 422 | x |  |",
                "| `MISSING_COL` | 422 | x |",
                "| `BARE_CONT` | 422 | x | ::foo |",
                "| `GARBAGE` | 422 | x | services/a.py:: |",
                "| `SAME_FILE_CONT` | 422 | x | services/b.py::first; ::second |",
            ]
        )
        anchors, problems = _anchors_from_section(section)
        assert ("SAME_FILE_CONT", "services/b.py", "first") in anchors
        assert ("SAME_FILE_CONT", "services/b.py", "second") in anchors
        joined = "\n".join(problems)
        assert "行号锚点已禁用" in joined, problems
        assert "抛出位置列为空" in joined, problems
        assert "缺「抛出位置」列" in joined, problems
        assert "之前没有文件锚点" in joined, problems
        assert "无法解析的锚点" in joined, problems

    def test_symbol_span_locates_nested_defs(self):
        """点分路径要下钻复合语句体：for / if 内定义的函数也是可定位符号（#521 评审）"""
        tree = ast.parse(
            "\n".join(
                [
                    "def outer():",
                    "    for _ in []:",
                    "        def nested():",
                    "            raise ValueError",
                    "        nested()",
                ]
            )
        )
        nested = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "nested"
        )
        assert _find_symbol_span(tree, "outer.nested") == (nested.lineno, nested.end_lineno)
        assert _find_symbol_span(tree, "outer.missing") is None

    def test_anchor_symbols_exist_and_emit_code(self):
        """每个锚点符号必须存在，且其行范围内确实有该码的抛出点"""
        anchors, problems = table_anchors()
        assert not problems, "总表锚点解析失败：\n" + "\n".join(problems)
        assert anchors, "未解析到任何锚点——解析器或总表格式已漂移"
        parsed: Dict[str, tuple] = {}
        for code, rel_file, symbol in anchors:
            if rel_file not in parsed:
                path = BACKEND_APP / rel_file
                if path.exists():
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                    parsed[rel_file] = (tree, _emissions_from_tree(tree))
                else:
                    parsed[rel_file] = (None, None)
            tree, emissions = parsed[rel_file]
            if tree is None:
                problems.append(f"`{code}`：锚点文件不存在：{rel_file}")
                continue
            span = _find_symbol_span(tree, symbol)
            if span is None:
                problems.append(f"`{code}`：锚点符号不存在：{rel_file}::{symbol}")
                continue
            if not any(c == code and span[0] <= line <= span[1] for c, line in emissions):
                problems.append(
                    f"`{code}`：{rel_file}::{symbol}（行 {span[0]}-{span[1]}）"
                    "行范围内没有该码的抛出点"
                )
        assert not problems, (
            f"总表锚点失配 {len(problems)} 处（符号不存在或符号行范围内不含该码抛出点）：\n"
            + "\n".join(problems)
        )
