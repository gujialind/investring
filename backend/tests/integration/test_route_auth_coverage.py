# ============================================================================
# 回归测试：全路由鉴权覆盖扫描（issue #256 配套防漏挂检查）
# ============================================================================
# market_data router 曾漏挂鉴权依赖（CWE-306）且无声通过所有门禁。
# 本测试遍历 app 全部路由：/api/* 端点必须（直接或间接）挂
# get_current_user / get_current_admin；确需公开的端点必须显式加入
# PUBLIC_API_PATHS 白名单，强制「公开即显式决策」。
#
# 遍历器必须版本无关（issue #306）：fastapi 0.141 起 app.routes 顶层存的是懒物化
# 代理（_IncludedRouter），isinstance(route, APIRoute) 命中 0 条 → 扫不到任何 /api
# 路由、missing 恒空、门禁静默假通过。故一律 duck-typing（不 import 私有类名）、
# 未知形状直接 raise，并由 test_scanner_is_not_running_empty 常驻兜住「扫到空/半空」
# 这一退化形态。
# ============================================================================

from collections import Counter

from app.main import app
from app.dependencies import get_current_user, get_current_admin

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


def _collect_dep_funcs(dependant) -> set:
    """递归收集 dependant 树上全部依赖函数（含子依赖）"""
    funcs = set()
    if dependant.call is not None:
        funcs.add(dependant.call)
    for sub in dependant.dependencies:
        funcs |= _collect_dep_funcs(sub)
    return funcs


def _funcs_from_depends(depends) -> set:
    """把 `include_router(dependencies=[...])` / 路由级 dependencies 摊成函数集合。

    这些依赖运行期本就合入 dependant 树，此处是纯防御：万一上游不再合并，
    靠 `include_router(dependencies=[Depends(get_current_user)])` 挂鉴权的端点仍算命中。
    """
    funcs = set()
    for dep in depends or []:
        target = getattr(dep, "dependency", dep)
        if callable(target):
            funcs.add(target)
    return funcs


def _join_prefix(prefix: str, path: str) -> str:
    """幂等拼接前缀：0.136 存的已是全路径、0.141 的 context.path 也是全路径，
    两者都不能重复拼；出现相对路径时（未来形状）才补前缀。
    """
    if not prefix or path == prefix or path.startswith(prefix + "/"):
        return path
    return prefix + path


def _children_of(node):
    children = getattr(node, "routes", None)
    if children is None:
        router = getattr(node, "router", None)
        children = getattr(router, "routes", None) if router is not None else None
    return children


def _scan(node, prefix: str = "", inherited_deps=frozenset()) -> list:
    """递归扫描路由树，返回 [(path, methods, dep_funcs)]。

    按「携带 dependant 的节点即一个操作」判定，不用 isinstance(APIRoute)——
    0.141 的有效路由上下文不是 APIRoute 实例但 dependant 可用。未知形状直接
    raise：宁可炸得响亮，也不静默少扫（#306 的根因正是静默少扫）。
    """
    operations = []

    contexts = getattr(node, "effective_route_contexts", None)
    if callable(contexts):  # fastapi >= 0.141 的懒物化代理
        include_ctx = getattr(node, "include_context", None)
        node_deps = inherited_deps | _funcs_from_depends(
            getattr(include_ctx, "dependencies", None)
        )
        node_prefix = _join_prefix(
            prefix, getattr(include_ctx, "prefix", "") or ""
        )
        for ctx in contexts():
            operations.append((
                _join_prefix(node_prefix, ctx.path),
                frozenset(ctx.methods or ()),
                _collect_dep_funcs(ctx.dependant)
                | node_deps
                | _funcs_from_depends(getattr(ctx, "dependencies", None)),
            ))
        return operations

    path = getattr(node, "path", None)
    if path is None:
        children = _children_of(node)
        if children is None:
            raise RuntimeError(
                f"未知路由节点形状：{type(node).__name__} 既无 .path 也无 "
                f"effective_route_contexts()，无法判定是否漏扫"
            )
        for child in children:
            operations.extend(_scan(child, prefix, inherited_deps))
        return operations

    full_path = _join_prefix(prefix, path)
    dependant = getattr(node, "dependant", None)
    if dependant is not None:  # fastapi <= 0.136 的 APIRoute（及同形状节点）
        operations.append((
            full_path,
            frozenset(node.methods or ()),
            _collect_dep_funcs(dependant) | inherited_deps,
        ))
        return operations

    children = _children_of(node)
    if children is not None:  # Mount / 子 Router：带上前缀继续下钻
        for child in children:
            operations.extend(_scan(child, full_path, inherited_deps))
        return operations

    if full_path.startswith("/api/"):
        raise RuntimeError(
            f"/api 节点 {full_path} 形状未知（既无 dependant 也无子路由），拒绝静默跳过"
        )
    return operations  # /docs、/openapi.json 等非业务路径不参与鉴权扫描


def _api_operations() -> list:
    """全量扫描 app 路由树，只保留 /api/* 操作"""
    app_deps = _funcs_from_depends(getattr(app, "dependencies", None))
    operations = []
    for node in app.routes:
        operations.extend(_scan(node, inherited_deps=frozenset(app_deps)))
    return [op for op in operations if op[0].startswith("/api/")]


class TestRouteAuthCoverage:
    """issue #256: 防止新 router 漏挂鉴权依赖；#306: 防止本扫描本身空转"""

    def test_all_api_routes_require_auth(self):
        missing = [
            f"{sorted(methods)} {path}"
            for path, methods, dep_funcs in _api_operations()
            if path not in PUBLIC_API_PATHS and not (dep_funcs & AUTH_DEPS)
        ]

        assert not missing, (
            "以下 /api 端点未挂鉴权依赖（get_current_user/get_current_admin）：\n  "
            + "\n  ".join(missing)
            + "\n确需公开的端点请显式加入 PUBLIC_API_PATHS 白名单"
        )

    def test_login_is_only_public_endpoint(self):
        """白名单自身体检：白名单内路径必须真实存在"""
        unknown = PUBLIC_API_PATHS - {path for path, _m, _d in _api_operations()}
        assert not unknown, f"白名单含不存在的端点：{unknown}"

    def test_scanner_is_not_running_empty(self):
        """防 #306：遍历退化（版本升级改结构）时 missing 恒空会假通过，
        故对扫描数量设下限并断言无重复项——漏拼/双拼都表现为重复或数量不足，
        而不是静默少算。
        """
        operations = _api_operations()
        keys = [(path, methods) for path, methods, _d in operations]
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
