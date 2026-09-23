# ============================================================================
# 单元测试守门：路由层 catch-all 翻 5xx 必须有日志出口（issue #553）
# ============================================================================
# 背景：`HTTPException` 由中间件链**内侧**的 `ExceptionHandlerMiddleware` 就地渲染成
# JSON 响应，永远冒不到**外层** `ServerErrorMiddleware` 里挂着 `Exception` handler 的
# 那一层（见 `app/main.py`）。于是 router 内「`except Exception` → 抛
# `HTTPException(5xx)`」这一族写法让全局 handler 完全失效：原始异常类型与堆栈被丢掉，
# stdout 无 ERROR 行、`system_error_log` 不落一行，事后只剩响应体里 `str(e)` 一句。
# 判据（扫描 `app/routers/**`，三条件同时成立才入场）：
#   ① 处理器是 catch-all（`except Exception` / `except BaseException` / 裸 `except:`）；
#   ② 处理器体内直接 `raise HTTPException(...)`，状态码落在 5xx——**含省略 status_code
#      时 HTTPException 的默认值 500**；
#   ③ 处理器体内没有任何 ERROR 级日志出口 → 判红，点名 `文件:行`。
# 日志出口的认定：`report_unexpected(...)`（本仓库统一入口，见
# `app/error_reporting.py`）或任何 `X.error/exception/critical(...)` 调用。
#
# **刻意不把判据放宽成「任何 except X → 5xx」**：422/404/409 是**有意的**领域映射，
# 给它们强制加 ERROR 出口只会把业务拒绝刷成噪音（#405 定的口径是 WARNING）。同理，
# `except TushareAPIError → 500` 这类窄捕获也不入场——它不在 AST 判据能稳定识别的
# 范围里，#553 已手工一并处理（trading_calendar.py），后续靠 code review。
#
# 防空转（守门自身的判红能力必须有反例验证，见本文件 TestScannerSelfCheck）：
#   - MIN_CATCHALL_SITES 下限棘轮：站点数被静默扫成 0（或扫描器解析退化）立即红；
#   - 合成源码用例：`TestScannerSelfCheck` 里那段「裸 catch-all 翻 500」必须被抓出，
#     否则本守门形同虚设；同时对「有出口」「翻 422」两种形态必须放过，避免只红不绿
#     的假守门（判红宽 + 判绿也宽 = 永远不响，同样是盲区）。
# ============================================================================

import ast
import re
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ROUTERS_DIR = BACKEND_ROOT / "app" / "routers"

# catch-all 的异常类型名（裸 `except:` 的 type 为 None，同样算 catch-all）
CATCH_ALL_NAMES = {"Exception", "BaseException"}
# 认可的 ERROR 级出口：`report_unexpected` 是统一入口，其余认 logger 的方法名
OUTLET_METHODS = {"error", "exception", "critical"}
OUTLET_FUNC_NAMES = {"report_unexpected"}

_STATUS_ATTR_RE = re.compile(r"^HTTP_(\d{3})")

# 防扫描退化的下限（**棘轮：等于当前实测站点数，只升不降**）。站点被删、或扫描器
# 静默失效（比如改坏了 raise/status 的识别）都会掉到线下：配合下面的判红自检，保证
# 「全绿」是真的绿。#553 修复后实测 10 处：snapshots 5 + market_data 3 + tasks 1 +
# trading_calendar 1。同期一并处理的 `except TushareAPIError → 500` 属窄捕获，按设计不入场。
MIN_CATCHALL_SITES = 10


@dataclass(frozen=True)
class Site:
    """一个「catch-all → 5xx」兜底站点。"""

    path: Path
    lineno: int
    status: int
    has_outlet: bool
    reason: str = ""


def _callee_name(node: ast.AST) -> str:
    """调用目标的末段名：`report_unexpected` / `status` / `HTTPException`…"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _status_literal(node: ast.AST) -> int | None:
    """状态码取值：整数字面量或 `status.HTTP_500_...` 形态的属性名；认不出返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Attribute):
        matched = _STATUS_ATTR_RE.match(node.attr)
        if matched:
            return int(matched.group(1))
    return None


def _http_exception_status(call: ast.Call) -> int:
    """HTTPException(...) 的实际状态码；省略 status_code 时取其默认值 500。"""
    for keyword in call.keywords:
        if keyword.arg == "status_code":
            status = _status_literal(keyword.value)
            return status if status is not None else 0  # 认不出当作非 5xx（不误伤）
    if call.args:
        status = _status_literal(call.args[0])
        if status is not None:
            return status
    return 500


def _is_outlet(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in OUTLET_FUNC_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in OUTLET_METHODS
    return False


def _is_catch_all(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:  # 裸 except:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in CATCH_ALL_NAMES
    if isinstance(handler.type, ast.Tuple):  # except (A, B): 里含 Exception 也算
        return any(isinstance(e, ast.Name) and e.id in CATCH_ALL_NAMES for e in handler.type.elts)
    return False


def _scan_source(source: str, *, path: Path) -> list[Site]:
    """扫描单份源码内的全部「catch-all → 5xx」站点。

    解析失败**直接抛出**（fail-closed）：「解析不了就跳过」会让守门在最需要它的时候
    静默失效——这是本仓库已有守门（`test_error_codes_doc_sync.py`）定下的铁律。
    """
    tree = ast.parse(source, filename=str(path))
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not _is_catch_all(node):
            continue
        raises = [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call)
            and _callee_name(child.exc.func) == "HTTPException"
        ]
        if not raises:
            continue
        statuses = {_http_exception_status(child.exc) for child in raises}
        if not any(500 <= s <= 599 for s in statuses):
            continue  # 翻的是 4xx/其它：有意的领域映射，不入场（见文件头）
        outlets = [child for child in ast.walk(node) if isinstance(child, ast.Call) and _is_outlet(child)]
        # 出口可能存在但没拿到原始异常（如 report_unexpected(None)）——error_type 会变成
        # NoneType，「500 的真因是什么」依旧没有答案，等同于没有出口
        bound = node.name  # `except Exception as e` 的绑定名
        reason = ""
        if not outlets:
            reason = "块内无任何 ERROR 级日志出口"
        elif bound and all(
            not (out.args and isinstance(out.args[0], ast.Name) and out.args[0].id == bound)
            for out in outlets
        ):
            reason = f"日志出口未收到原始异常（缺第一个实参 `{bound}`），error_type 会失真"
        sites.append(
            Site(
                path=path,
                lineno=node.lineno,
                status=max(statuses),
                has_outlet=not reason,
                reason=reason,
            )
        )
    return sites


def scan_routers() -> list[Site]:
    """扫描 `app/routers/**`（fail-closed：目录不存在或为空也算扫描退化）。"""
    files = sorted(ROUTERS_DIR.rglob("*.py"))
    assert files, f"{ROUTERS_DIR} 下没有 .py 文件——扫描路径写错了？"
    sites: list[Site] = []
    for file in files:
        sites.extend(_scan_source(file.read_text(encoding="utf-8"), path=file))
    return sites


def _rel(site: Site) -> str:
    try:
        return f"{site.path.relative_to(BACKEND_ROOT.parent)}:{site.lineno}"
    except ValueError:
        return f"{site.path}:{site.lineno}"


class TestCatchAllLoggingOutlet:
    """生产码：每个 catch-all 翻 5xx 的分支都必须留下可追溯线索"""

    def test_site_count_ratchet(self):
        """站点数下限——扫描器退化（扫成 0 或识别失效）必须响亮红，而不是静默全绿"""
        count = len(scan_routers())
        assert count >= MIN_CATCHALL_SITES, (
            f"扫到的 catch-all→5xx 站点仅 {count} 处，低于下限 {MIN_CATCHALL_SITES}："
            "要么兜底被删了（删前请确认它的日志出口已无必要），"
            "要么扫描器识别失效（见 _scan_source）"
        )

    def test_every_catchall_has_outlet(self):
        offenders = [s for s in scan_routers() if not s.has_outlet]
        assert not offenders, (
            "以下 catch-all 把非预期异常翻成 5xx 却没有日志出口（#553：HTTPException 走的是"
            "中间件链内侧，外层全局 handler 接不到，500 事后不可追溯）：\n"
            + "\n".join(f"    {_rel(s)} —— {s.reason}（HTTP {s.status}）" for s in offenders)
            + "\n修法：在 raise 之前调 `report_unexpected(e, operation=\"<端点名>\")`，"
            "或自行 logger.error(..., exc_info=e)；响应契约与错误码不因此改变。"
        )

    def test_report_unexpected_is_wired(self):
        """统一出口必须真的被 router 用上——防止「各处自己 logger.error，台账对不上」"""
        usages = 0
        for file in ROUTERS_DIR.rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            usages += sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and _callee_name(node.func) == "report_unexpected"
            )
        assert usages >= MIN_CATCHALL_SITES, (
            f"report_unexpected 调用点 {usages} 少于兜底站点数 {MIN_CATCHALL_SITES}——"
            "有站点没走统一出口（各自零散记日志会让 system_error_log 的覆盖面失去单一口径）"
        )


class TestScannerSelfCheck:
    """守门自身的判红能力（同 #521/#539 范式：探针必须有反例，否则形同虚设）"""

    SOURCE_RED = '''
from fastapi import HTTPException

def endpoint():
    try:
        do_work()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
'''

    SOURCE_GREEN = '''
from fastapi import HTTPException
from starlette import status

def endpoint():
    try:
        do_work()
    except Exception as e:
        report_unexpected(e, operation="endpoint")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
'''

    SOURCE_NOT_OUR_BUSINESS = '''
from fastapi import HTTPException
from starlette import status

def endpoint():
    try:
        do_work()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
'''

    SOURCE_WRONG_ARG = '''
from fastapi import HTTPException

def endpoint():
    try:
        do_work()
    except Exception as e:
        report_unexpected(None, operation="endpoint")
        raise HTTPException(status_code=500, detail="boom")
'''

    def _scan(self, source: str) -> list[Site]:
        return _scan_source(source, path=Path("synthetic.py"))

    def test_bare_catchall_turn_red(self):
        """插入一处裸 catch-all 翻 500 → 必须被点名（否则本守门对真实回退毫无用处）"""
        sites = self._scan(self.SOURCE_RED)
        assert len(sites) == 1, f"未识别出这段源码里的裸 catch-all 站点：{sites}"
        assert not sites[0].has_outlet
        assert "日志出口" in sites[0].reason

    def test_outlet_turns_green(self):
        """接了 report_unexpected 的同形态站点须放过——只红不绿的探针会逼人绕过守门"""
        assert self._scan(self.SOURCE_GREEN)[0].has_outlet

    def test_default_500_is_counted(self):
        source = self.SOURCE_RED.replace("status_code=500, ", "")
        assert self._scan(source), "省略 status_code 时 HTTPException 默认 500，必须同样入场"

    def test_domain_mapping_out_of_scope(self):
        """翻 422 的不入场：守门放宽成「任何 except → 5xx」会把业务拒绝刷成 ERROR 噪音"""
        assert self._scan(self.SOURCE_NOT_OUR_BUSINESS) == []

    def test_outlet_without_original_exception_turns_red(self):
        """出口没拿到原始异常 → error_type 失真，等同没有出口（#553 教训的另一半）"""
        sites = self._scan(self.SOURCE_WRONG_ARG)
        assert sites and not sites[0].has_outlet, f"未识别失真出口：{sites}"

    def test_parse_failure_fails_closed(self):
        """解析失败必须抛出（fail-closed），不允许「扫不动就跳过」的静默失效"""
        import pytest

        with pytest.raises(SyntaxError):
            _scan_source("def boom(:\n    pass\n", path=Path("broken.py"))
