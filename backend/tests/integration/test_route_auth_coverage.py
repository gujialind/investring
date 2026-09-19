# ============================================================================
# 回归测试：全路由鉴权覆盖扫描（issue #256 配套防漏挂检查）
# ============================================================================
# market_data router 曾漏挂鉴权依赖（CWE-306）且无声通过所有门禁。
# 本测试遍历 app 全部路由：/api/* 端点必须（直接或间接）挂
# get_current_user / get_current_admin；确需公开的端点必须显式加入
# PUBLIC_API_PATHS 白名单，强制「公开即显式决策」。
#
# 路由遍历器见 tests/integration/route_scan_helpers.py（版本无关 duck-typing，
# 含 issue #306 的假通过防线），本文件不再内联扫描实现。
# ============================================================================

from collections import Counter

from app.main import app
from app.dependencies import get_current_user, get_current_admin

from tests.integration.route_scan_helpers import api_operations

# 合法公开端点白名单：新增公开端点必须有意识地加入此列表
PUBLIC_API_PATHS = {
    "/api/auth/login",
}

AUTH_DEPS = {get_current_user, get_current_admin}

# 扫描器应命中的 /api 操作数下限（操作 = 一个路由对象，非 distinct path：同一 path
# 挂 GET/PUT 算两条）。维护规则：新增端点时同步上调；下调必须有理由。
# 取 fastapi 0.136.1 与 0.141.1 双版本实测的相同值（85 个 distinct path、112 个操作）；
# #424 新增 GET /api/share-change-events/{id}/preview → 86 distinct path / 113 操作。
EXPECTED_API_OPERATIONS = 113


class TestRouteAuthCoverage:
    """issue #256: 防止新 router 漏挂鉴权依赖；#306: 防止本扫描本身空转"""

    def test_all_api_routes_require_auth(self):
        missing = [
            f"{sorted(op.methods)} {op.path}"
            for op in api_operations(app)
            if op.path not in PUBLIC_API_PATHS and not (op.dep_funcs & AUTH_DEPS)
        ]

        assert not missing, (
            "以下 /api 端点未挂鉴权依赖（get_current_user/get_current_admin）：\n  "
            + "\n  ".join(missing)
            + "\n确需公开的端点请显式加入 PUBLIC_API_PATHS 白名单"
        )

    def test_login_is_only_public_endpoint(self):
        """白名单自身体检：白名单内路径必须真实存在"""
        unknown = PUBLIC_API_PATHS - {op.path for op in api_operations(app)}
        assert not unknown, f"白名单含不存在的端点：{unknown}"

    def test_scanner_is_not_running_empty(self):
        """防 #306：遍历退化（版本升级改结构）时 missing 恒空会假通过，
        故对扫描数量设下限并断言无重复项——漏拼/双拼都表现为重复或数量不足，
        而不是静默少算。
        """
        operations = api_operations(app)
        keys = [(op.path, op.methods) for op in operations]
        duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
        assert not duplicates, (
            "扫描器产出重复项（path 前缀漏拼或双拼）：\n  "
            + "\n  ".join(f"{sorted(methods)} {path}" for path, methods in duplicates)
        )
        assert len(operations) >= EXPECTED_API_OPERATIONS, (
            f"扫描到 {len(operations)} 个 /api 操作，低于下限 {EXPECTED_API_OPERATIONS}。"
            "两种可能：①端点被删（确认理由后可下调 EXPECTED_API_OPERATIONS）；"
            "②fastapi/starlette 升级改了 app.routes 结构、本扫描器已扫不到路由"
            "（正是 issue #306 的假通过形态，必须修扫描器而非改数字）。"
            f"当前 app.routes 顶层节点类型：{sorted({type(r).__name__ for r in app.routes})}"
        )
