"""
ir-cli 手册/schema 承诺一致性守门（issue #562）

2026-09-19 审查复盘 S3：ir-cli 的**文档承诺面**（`CLI_MANUAL.md` 的选项表与退出码说明、
`ir schema` 的 WORKFLOWS 配方）与**实现面**（Click 命令选项、`output.py` 的 `EXIT_*`）之间
没有一致性守门，窗口内同一处漂移被人工抓出 3 次。本文件把这三条只存在于人脑里的默会知识
（institutional knowledge）变成四条**方向明确**的等价断言：

1. **配方选项存在性**：`schema.WORKFLOWS` 每个 step 里的 `--opt` 必须在该命令的 Click 选项面上
   （拼错/已删选项 ⇒ 红）
2. **配方必填齐备**：step 引用到的命令，其必填选项（Click `required=True` ∪ 手册选项表标「是」）
   必须逐个出现在该 step 里（#527 的缺陷形态）
3. **手册选项表 ↔ 实现双向**：手册 §4 每个命令条目登记的 `--xxx` 必须存在；反向亦然，
   （例外需落进 `KNOWN_*` 豁免表并注明理由；豁免一旦过期不再命中 ⇒ 红）
4. **退出码闭包**：手册声明的码集合 == `output.py` 的 `EXIT_*` 常量集合（+0 成功），
   且两两不相等（撞码即红，#520 的直接泛化）

**解析口径一律 fail-closed**：抽不到就红。禁止「解析失败即跳过」——那会让本文件自己变成
复盘 F1 族的第 15 项。`TestParsersFailClosed` 钉住这一点。

**反例自检**：每条断言都配一条合成输入使其变红。反例**内联在各自的断言组里**，不单独成类：
`TestRecipeOptionExistence::test_typo_in_recipe_option_is_red`、
`TestRecipeRequiredCompleteness::test_dropping_a_required_option_is_red`、
`TestManualOptionParity::test_unknown_manual_option_is_red` /
`test_undocumented_impl_option_is_red` / `test_stale_exemption_is_red`、
`TestExitCodeClosure::test_colliding_exit_code_is_red`。
某条断言如果对反例不红，说明它恒真 ⇒ 视为未完成（#502 💭-1 的同型失效）。

运行方式（ir-cli/.venv 无 pytest，用仓库根 .venv）：
    PYTHONPATH=ir-cli .venv/bin/python -m pytest ir-cli/tests -q
"""
import re
from collections import Counter, namedtuple
from pathlib import Path

import pytest
import typer

from ir_cli import output
from ir_cli.main import app
from ir_cli.schema import CONVENTIONS, PROTOCOL, WORKFLOWS

MANUAL_PATH = Path(__file__).resolve().parents[1] / "CLI_MANUAL.md"

OPTION_RE = re.compile(r"--[a-z][a-z0-9-]*")
TOKEN_RE = re.compile(r"^[a-z][a-z0-9-]*$")
PATH_CHAR_RE = re.compile(r"[a-z0-9-]")
CODE_RE = re.compile(r"exit code (\d+)|(\d+)\s*=")
EXIT_ANCHOR_RE = re.compile(r"退出码|exit code")

SECTION4 = "## 4."
SECTION5 = "## 5."

# 通用约定选项：全局章节统一登记，不逐命令进选项表
# （`TestManualOptionParity::test_global_options_are_documented_before_section4` 反向钉住这一点）
GLOBAL_OPTIONS = {key for key in CONVENTIONS if key.startswith("--")}

CommandSpec = namedtuple("CommandSpec", "options required")
Section = namedtuple("Section", "heading paths options required")
Step = namedtuple("Step", "workflow index path options complete")


# --------------------------------------------------------------------------- #
# 实现侧：从 Click 取真值
# --------------------------------------------------------------------------- #
def click_command_specs() -> dict:
    """命令路径 → CommandSpec(所有 -- 选项, 必填 -- 选项)；递归展开嵌套命令组"""
    specs = {}

    def walk(cmd, path):
        subs = getattr(cmd, "commands", None)
        if subs is None:
            options, required = set(), set()
            for param in cmd.params:
                if param.name == "help":
                    continue
                opts = [o for o in getattr(param, "opts", []) if o.startswith("--")]
                if not opts:
                    continue
                options.update(opts)
                if param.required:
                    required.update(opts)
            specs[" ".join(path)] = CommandSpec(options, required)
            return
        for name, sub in subs.items():
            walk(sub, path + [name])

    walk(typer.main.get_command(app), ["ir"])
    return specs


def _longest_first(paths) -> list:
    """路径长者优先，保证 `ir snapshot generate-next` 不被 `ir snapshot generate` 抢走"""
    return sorted(paths, key=lambda p: (-len(p.split()), p))


def _match_path(text: str, start: int, paths) -> str:
    """在 text[start:] 匹配一个命令路径；要求其后不是路径字符，避免前缀误命中"""
    for path in paths:
        if not text.startswith(path, start):
            continue
        nxt = start + len(path)
        if nxt == len(text) or not PATH_CHAR_RE.match(text[nxt]):
            return path
    return ""


# --------------------------------------------------------------------------- #
# 文档侧：轻量正则抽取（保守，抽不到不静默）
# --------------------------------------------------------------------------- #
def _manual_lines() -> list:
    return MANUAL_PATH.read_text(encoding="utf-8").split("\n")


def _slice(lines: list, start_marker: str, end_marker: str) -> list:
    start = next((i for i, l in enumerate(lines) if l.startswith(start_marker)), None)
    if start is None:
        return []
    tail = lines[start + 1:]
    cut = next((i for i, l in enumerate(tail) if l.startswith(end_marker)), len(tail))
    return tail[:cut]


def _option_cell(line: str) -> str:
    """表格首格为 `--xxx`（可含 `--a/--b` 连写）时返回第一个选项名"""
    m = re.match(r"^\|\s*`(--[a-z0-9-]+)", line)
    return m.group(1) if m else ""


def parse_manual_sections(text: str) -> list:
    """抽取手册 §4「命令详解」的每个命令条目块

    块 = `###`/`####` 标题到下一个标题之间；选项来源二选一：
    - ```bash 围栏内以 `ir ` 开头的用法行（含下一行以 `[` 起始的续行）
    - 首格为 `--xxx` 的表格行
    必填来源：表格头里标「必填」的那一列取值为「是」的行。
    """
    sections, current = [], None
    in_fence, open_cmd, req_col = False, None, None
    require_row = False  # 下一行是否为表头分隔行

    for line in text.split("\n"):
        if line.startswith("### ") or line.startswith("#### "):
            current = Section(line, [], set(), set())
            sections.append(current)
            in_fence, open_cmd, req_col, require_row = False, None, None, False
            continue
        if current is None:
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            open_cmd = None
            continue

        stripped = line.strip()
        if in_fence:
            if stripped.startswith("ir "):
                tokens = []
                for token in stripped.split():
                    if TOKEN_RE.match(token):
                        tokens.append(token)
                    else:
                        break
                open_cmd = " ".join(tokens)
                current.paths.append(open_cmd)
                current.options.update(OPTION_RE.findall(stripped))
            elif open_cmd and stripped.startswith("["):
                # 多行用法行的续行（如 `ir sub list` 的第二行）
                current.options.update(OPTION_RE.findall(stripped))

        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            option = _option_cell(stripped)
            if option:
                current.options.add(option)
                if req_col is not None and req_col < len(cells) and cells[req_col] == "是":
                    current.required.add(option)
            elif "必填" in cells:
                req_col = cells.index("必填")
                require_row = True
            elif require_row:
                require_row = False
    return sections


def parse_manual_exit_codes(text: str) -> tuple:
    """返回 (声明的退出码集合, 命中的声明行列表)；抽不到 ⇒ 空集合（由用例判红）"""
    anchors, codes = [], set()
    for line in text.split("\n"):
        if not EXIT_ANCHOR_RE.search(line):
            continue
        found = {int(a or b) for a, b in CODE_RE.findall(line)}
        if found:
            anchors.append(line)
            codes |= found
    return codes, anchors


def manual_required_map(sections: list) -> dict:
    """命令路径 → 手册选项表声明的必填选项"""
    result = {}
    for section in sections:
        for path in section.paths:
            result.setdefault(path, set()).update(section.required)
    return result


# --------------------------------------------------------------------------- #
# 配方侧：step 字符串 → Step
# --------------------------------------------------------------------------- #
def parse_recipe_steps(workflows: dict, known_paths) -> list:
    """把每条 step 切成「命令 + 该命令对应的选项」

    - 用已知命令路径做最长匹配定位命令，段尾到下一个命中为止
    - `complete=False` 表示该 step 是**刻意节选**（含 `...` 或与另一条命令用「或」并列的讨论式写法），
      不参与必填齐备校验；]]]"""
    paths = _longest_first(known_paths)
    steps = []
    for workflow, body in workflows.items():
        for index, raw in enumerate(body["steps"]):
            hits = []
            cursor = 0
            while cursor < len(raw):
                path = _match_path(raw, cursor, paths)
                if path:
                    hits.append((cursor, path))
                    cursor += len(path)
                else:
                    cursor += 1
            if not hits:
                # 纯散文 step（如「确保 entitlement_date 当日快照已存在」）
                steps.append(Step(workflow, index, "", set(OPTION_RE.findall(raw)), False))
                continue
            for i, (start, path) in enumerate(hits):
                end = hits[i + 1][0] if i + 1 < len(hits) else len(raw)
                segment = raw[start:end]
                steps.append(Step(
                    workflow, index, path, set(OPTION_RE.findall(segment)),
                    complete="..." not in segment and "或" not in raw,
                ))
    return steps


# --------------------------------------------------------------------------- #
# 四条判据：入参为解析结果，返回值非空即为红（便于合成反例）
# --------------------------------------------------------------------------- #
def check_recipe_option_existence(steps: list, specs: dict) -> list:
    """断言 1：每个 step 里的 --opt 必须在对应命令的选项面上"""
    known = set()
    for spec in specs.values():
        known |= spec.options
    violations = []
    for step in steps:
        if not step.path:
            # 散文 step 没有归属命令，退一步用全量并集验：仍能抓到拼错的选项名
            missing = step.options - known
            if missing:
                violations.append(
                    f"{step.workflow} step{step.index + 1}（无命令归属）不存在选项: "
                    f"{sorted(missing)}"
                )
            continue
        missing = step.options - specs[step.path].options
        if missing:
            violations.append(
                f"{step.workflow} step{step.index + 1} {step.path} 不存在选项: {sorted(missing)}"
            )
    return violations


def check_recipe_required(steps: list, required: dict) -> list:
    """断言 2：必填选项必须逐个出现在完整 step 里"""
    violations = []
    for step in steps:
        if not step.path or not step.complete:
            continue
        missing = required.get(step.path, set()) - step.options
        if missing:
            violations.append(
                f"{step.workflow} step{step.index + 1} {step.path} 缺必填: {sorted(missing)}"
            )
    return violations


def check_manual_option_parity(sections: list, specs: dict, known_manual_only: dict,
                               known_impl_only: dict) -> list:
    """断言 3：手册 §4 选项登记 ↔ 实现选项面双向比对（含豁免过期检查）"""
    violations, observed = [], {"manual-only": set(), "impl-only": set()}
    known_paths = _longest_first(specs.keys())
    for section in sections:
        # 用法行可能顺带位置参数（如 `ir system datasource-update tushare --api-key ...`，
        # 解析出的 path 会带上 `tushare`）。用整串去 specs 取命令 ⇒ 该命令静默退出比对，
        # 它的选项既不报漂移也不算命中豁免，守门沦为摆设——故先规约到真实命令路径。
        paths = sorted({hit for p in section.paths if (hit := _match_path(p, 0, known_paths))})
        union = set()
        for path in paths:
            union |= specs[path].options
        for path in paths:
            impl = specs[path].options
            manual_only = section.options - impl
            if len(paths) > 1:
                # 多子命令共用一张参数表（如 `ir system trading-day`）时按块归并
                manual_only -= union
            for option in sorted(manual_only):
                observed["manual-only"].add((path, option))
                reason = known_manual_only.get(path, {}).get(option)
                if not reason:
                    violations.append(
                        f"{path}: 手册登记 {option} 但实现无此选项"
                        f"（{section.heading.strip()}）"
                    )
            for option in sorted(impl - section.options - GLOBAL_OPTIONS):
                observed["impl-only"].add((path, option))
                reason = known_impl_only.get(path, {}).get(option)
                if not reason:
                    violations.append(
                        f"{path}: 实现有 {option} 但手册未登记"
                        f"（{section.heading.strip()}）"
                    )
    for (path, option), _ in ((key, reason)
                              for key, reason in [(k, v) for d in [known_manual_only]
                                                  for k, v in _flatten(d, "manual-only")]):
        pass
    for key in _entries(known_manual_only, "manual-only"):
        if key not in observed["manual-only"]:
            violations.append(f"豁免已过期（手册登记侧）: {key[0]} {key[1]} —— 漂移已消失，请删条目")
    for key in _entries(known_impl_only, "impl-only"):
        if key not in observed["impl-only"]:
            violations.append(f"豁免已过期（实现未登记侧）: {key[0]} {key[1]} —— 漂移已消失，请删条目")
    return violations


def _flatten(mapping, side):
    return [((cmd, option), reason) for cmd, opts in mapping.items() for option, reason in opts.items()]


def _entries(mapping, side):
    return [(cmd, option) for cmd, opts in mapping.items() for option in opts]


def check_exit_code_closure(manual_codes: set, exit_constants: dict,
                            protocol_codes: set = None) -> list:
    """断言 4：手册声明 == 常量集合(+0)，三者互校，且两两不相等"""
    violations = []
    impl = set(exit_constants.values()) | {0}
    for side, extra in (("手册未声明", impl - manual_codes), ("实现未定义", manual_codes - impl)):
        for code in sorted(extra):
            violations.append(f"退出码闭包不一致 {side}: {code}")
    if protocol_codes is not None:
        for side, extra in (("schema PROTOCOL 未声明", manual_codes - protocol_codes),
                            ("手册未声明(schema 侧)", protocol_codes - manual_codes)):
            for code in sorted(extra):
                violations.append(f"退出码闭包不一致 {side}: {code}")
    counts = Counter(exit_constants.values())
    for code, times in sorted(counts.items()):
        if times > 1:
            names = sorted(name for name, value in exit_constants.items() if value == code)
            violations.append(f"退出码撞码 {code}: {names}")
    if 0 in counts:
        violations.append("退出码撞码 0: EXIT_* 常量不得占用成功码 0")
    return violations


# --------------------------------------------------------------------------- #
# 已知漂移豁免表：探针首跑（2026-09-23，HEAD=bff1769）全量比对得出。
# 每条必填理由；漂移一旦对齐 ⇒ 断言转红要求删条目（`check_manual_option_parity` 反向检查）。
# --------------------------------------------------------------------------- #
KNOWN_MANUAL_ONLY = {
    # 手册登记了、实现没有（或已改名）——多为近期重构后手册未跟随
    "ir investor create": {"--role": "手册 4.2 登记 --role（默认 viewer），实现 create 未开放，仅 update 有"},
    "ir investor update": {"--password": "手册用法行写 --password <新密码>，实现 update 无密码选项（改密码走 ir auth change-password）"},
    "ir share-event create": {
        "--event-source": "手册 4.7 登记 --event-source，实现未开放",
        "--shares-after": "手册登记 --shares-after，实现已改名 --shares-change",
        "--shares-before": "手册登记 --shares-before，实现已改名 --entitlement-shares",
    },
    "ir share-event update": {"--entitlement-shares": "手册 4.7 update 登记 --entitlement-shares（属 create），实现 update 无此选项"},
    "ir snapshot recalculate": {"--force": "手册登记 --force，实现改为 --async/--wait/--poll-interval 异步重算"},
    "ir product create": {"--data-source": "手册 4.9 登记 --data-source，实现 create 未开放"},
    "ir product update": {
        "--data-source": "手册 4.9 登记 --data-source，实现 update 未开放",
        "--no-qdii": "手册写 --is-qdii/--no-qdii 双开关，实现只注册 --is-qdii",
    },
    "ir sub confirm": {
        "--confirm-date": "手册 4.5 登记 --confirm-date，实现 confirm 只有 --quiet + id（确认日由后端推定）",
        "--unit-price": "手册 4.5 登记 --unit-price，实现 confirm 未开放（净值由后端取 T 日）",
    },
    "ir cash-transfer confirm": {"--group": "手册写 --group <transfer_group>，实现为位置参数 transfer_group"},
    "ir asset-classification update": {"--inactive": "手册 4.18 与说明均承诺 --active/--inactive 软失效，实现只注册 --active"},
}

KNOWN_IMPL_ONLY = {
    # 实现有、手册未登记——多为后加参数未回写手册
    "ir sub create": {
        "--confirm": "手册 4.5 未登记 --confirm（创建后直接确认）",
        "--platform-code": "手册 4.5 未登记 --platform-code（#527 同形态）",
        "--unit-price": "手册 4.5 未登记 --unit-price（指定确认净值）",
    },
    "ir trade create": {
        "--allow-duplicate": "手册 4.6 未登记 --allow-duplicate（放行重复交易告警）",
        "--confirm": "手册 4.6 未登记 --confirm（创建后直接确认）",
    },
    "ir share-event update": {
        "--entitlement-date": "手册 4.7 未登记 --entitlement-date",
        "--shares-change": "手册 4.7 未登记 --shares-change",
    },
    "ir product list": {
        "--data-source": "手册 4.9 未登记 --data-source 过滤",
        "--data-source-status": "手册 4.9 未登记 --data-source-status 过滤",
        "--market": "手册 4.9 未登记 --market 过滤",
    },
    "ir system calendar": {"--no-cache": "手册 4.12 未登记 --no-cache"},
    "ir system datasource-update": {"--is-enabled": "手册 4.12 未登记 --is-enabled"},
}


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def manual_text():
    return MANUAL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def specs():
    return click_command_specs()


@pytest.fixture(scope="module")
def sections(manual_text):
    return parse_manual_sections(_slice(_manual_lines(), SECTION4, SECTION5)
                                 and "\n".join(_slice(_manual_lines(), SECTION4, SECTION5)))


@pytest.fixture(scope="module")
def steps(specs):
    return parse_recipe_steps(WORKFLOWS, specs.keys())


@pytest.fixture(scope="module")
def required_map(sections, specs):
    """必填口径 = Click required=True ∪ 手册选项表标「是」"""
    merged = {path: set(spec.required) for path, spec in specs.items()}
    for path, manual_req in manual_required_map(sections).items():
        if path in merged:
            merged[path] |= manual_req
    return merged


# --------------------------------------------------------------------------- #
# 解析口径 fail-closed：抽不到就红（禁止写成「解析失败即跳过」）
# --------------------------------------------------------------------------- #
class TestParsersFailClosed:
    def test_click_tree_is_complete(self, specs):
        # 19 个命令组 + 顶层 schema；命令面塌掉会让下面三条断言集体空转
        assert len(specs) >= 100, f"Click 命令树仅解析到 {len(specs)} 个叶子命令"
        assert "ir system trading-day next" in specs  # 嵌套组必须递归到
        assert specs["ir trade create"].options  # 选项面非空

    def test_manual_section4_is_parsed(self, sections, specs):
        assert sections, "手册 §4 未抽到任何命令条目——章节标题或围栏格式变了"
        covered = {p for s in sections for p in s.paths if p in specs}
        assert len(covered) >= 90, f"§4 仅命中 {len(covered)} 个真实命令"

    def test_every_recipe_yields_a_command(self, steps):
        for workflow in WORKFLOWS:
            assert any(s.path for s in steps if s.workflow == workflow), \
                f"配方「{workflow}」没有任何 step 解析出命令——step 写法变了"

    def test_required_check_covers_real_steps(self, steps):
        # 防止节选规则放宽到把完整示例也判成节选，使断言 2 恒真
        checked = [s for s in steps if s.path and s.complete]
        assert len(checked) >= 10, f"仅 {len(checked)} 条 step 进入必填校验，口径过松"

    def test_exit_code_anchors_are_found(self, manual_text):
        codes, anchors = parse_manual_exit_codes(manual_text)
        assert len(anchors) >= 2, "手册退出码声明行少于 2 处（§3.2 / §6.2）"
        assert codes, "退出码声明行抽不到码——表述格式变了"


# --------------------------------------------------------------------------- #
# 断言 1 / 2：配方
# --------------------------------------------------------------------------- #
class TestRecipeOptionExistence:
    def test_recipe_options_exist_on_click_face(self, steps, specs):
        assert check_recipe_option_existence(steps, specs) == []

    def test_typo_in_recipe_option_is_red(self, specs):
        """反例：给配方加一个不存在的 --nope ⇒ 断言 1 必须红"""
        mutated = {
            "mock": {"steps": ["ir trade create --portfolio-code X --nope 1"], "notes": ""},
        }
        steps = parse_recipe_steps(mutated, specs.keys())
        violations = check_recipe_option_existence(steps, specs)
        assert violations and "--nope" in violations[0]


class TestRecipeRequiredCompleteness:
    def test_recipe_steps_carry_required_options(self, steps, required_map):
        assert check_recipe_required(steps, required_map) == []

    def test_dropping_a_required_option_is_red(self, specs, required_map):
        """反例：删掉必填的 --apply-date ⇒ 断言 2 必须红"""
        mutated = {
            "mock": {
                "steps": ["ir sub create --portfolio-code X --investor-code I --type subscribe --amount N"],
                "notes": "",
            },
        }
        steps = parse_recipe_steps(mutated, specs.keys())
        violations = check_recipe_required(steps, required_map)
        assert violations and "--apply-date" in violations[0]


# --------------------------------------------------------------------------- #
# 断言 3：手册选项表 ↔ 实现
# --------------------------------------------------------------------------- #
class TestManualOptionParity:
    def test_manual_and_implementation_agree(self, sections, specs):
        assert check_manual_option_parity(sections, specs, KNOWN_MANUAL_ONLY, KNOWN_IMPL_ONLY) == []

    def test_unknown_manual_option_is_red(self, specs):
        """反例：手册里加一行假选项 ⇒ 断言 3 必须红"""
        fake = [Section("#### `ir trade list`", ["ir trade list"], {"--nope"}, set())]
        violations = check_manual_option_parity(fake, specs, {}, {})
        assert [v for v in violations if "--nope" in v]

    def test_undocumented_impl_option_is_red(self, specs):
        """反例：实现多出一个手册没写的选项 ⇒ 断言 3 必须红"""
        mutated = dict(specs)
        mutated["ir trade list"] = CommandSpec(specs["ir trade list"].options | {"--nope"}, set())
        fake = [Section("#### `ir trade list`", ["ir trade list"], set(), set())]
        violations = check_manual_option_parity(fake, mutated, {}, {})
        assert [v for v in violations if "--nope" in v]

    def test_stale_exemption_is_red(self, specs):
        """反例：豁免表里的漂移已消失却没删条目 ⇒ 断言 3 必须红（防止豁免永久化）"""
        fake = [Section("#### `ir trade list`", ["ir trade list"], set(), set())]
        violations = check_manual_option_parity(
            fake, specs, {"ir trade list": {"--status": "假装还有漂移"}}, {},
        )
        assert [v for v in violations if "豁免已过期" in v]

    def test_positional_arg_in_usage_line_does_not_hide_command(self, specs):
        """反例：用法行带位置参数时，该命令仍须参与比对

        `ir system datasource-update tushare --api-key <x>` 的 path 被解析成带 `tushare`
        的整串，若径直用它去 specs 取命令，该命令就**静默退出比对**——它的漂移既不报
        红，也不命中豁免（后者还会被反判「豁免已过期」）。守门自盲比漏报更危险，
        故钉住：此时未登记的 `--is-enabled` 必须被报出来。
        """
        text = (
            "#### `ir system datasource-update`\n\n"
            "```bash\nir system datasource-update tushare --api-key <x>\n```\n"
        )
        sections = parse_manual_sections(text)
        violations = check_manual_option_parity(sections, specs, {}, {})
        hits = [v for v in violations if v.startswith("ir system datasource-update")]
        assert hits and "--is-enabled" in hits[0]

    def test_global_options_are_documented_before_section4(self, manual_text):
        """全局约定选项不逐命令登记，但必须在 §4 之前的章节里出现，否则放行等于放水"""
        head = manual_text.split(SECTION4, 1)[0]
        for option in sorted(GLOBAL_OPTIONS):
            assert option in head, f"通用选项 {option} 未在 §4 之前的手册正文中说明"


# --------------------------------------------------------------------------- #
# 断言 4：退出码闭包
# --------------------------------------------------------------------------- #
class TestExitCodeClosure:
    def test_manual_matches_exit_constants(self, manual_text):
        codes, _ = parse_manual_exit_codes(manual_text)
        exit_constants = {n: v for n, v in vars(output).items() if n.startswith("EXIT_")}
        protocol = {int(k) for k in PROTOCOL["exit_codes"]}
        assert check_exit_code_closure(codes, exit_constants, protocol) == []

    def test_exit_constants_are_pairwise_distinct(self):
        exit_constants = {n: v for n, v in vars(output).items() if n.startswith("EXIT_")}
        codes, _ = parse_manual_exit_codes(MANUAL_PATH.read_text(encoding="utf-8"))
        assert check_exit_code_closure(codes, exit_constants) == []
        assert len(set(exit_constants.values())) == len(exit_constants)

    def test_colliding_exit_code_is_red(self, manual_text):
        """反例：EXIT_AUTH 撞成 1 ⇒ 断言 4 必须红（#520 的同型回归）"""
        codes, _ = parse_manual_exit_codes(manual_text)
        constant_names = [n for n in vars(output) if n.startswith("EXIT_")]
        mutated = {n: (1 if n == "EXIT_AUTH" else getattr(output, n)) for n in constant_names}
        violations = check_exit_code_closure(codes, mutated)
        assert violations
        assert any("撞码" in v or "不一致" in v for v in violations)
