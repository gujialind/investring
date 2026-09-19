# ============================================================================
# 路由扫描共享 helper（自 tests/integration/test_route_auth_coverage.py 抽取，issue #513）
# ============================================================================
# 溯源：扫描器原属 test_route_auth_coverage.py（issue #256 全路由鉴权覆盖；#306 修
# 「版本升级后扫到空/半空仍静默假通过」）。
# 现状：被 test_route_auth_coverage.py（鉴权覆盖）与 test_response_model_guard.py
# （响应模型守门）共用，故提取到本模块；无 test_ 前缀，pytest 不收集。
#
# 遍历器必须版本无关（#306）：fastapi 0.141 起 app.routes 顶层存的是懒物化代理
# （_IncludedRouter），isinstance(route, APIRoute) 命中 0 条 → 扫不到任何 /api 路由，
# 门禁静默假通过。故一律 duck-typing（不 import 私有类名）、未知形状直接 raise。
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass

from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class RouteOperation:
    """路由树上的一个操作（一个路由对象；同一 path 挂多方法算多条）。

    response_class 已解包 fastapi 的 DefaultPlaceholder（0.136/0.141 未显式指定时
    存的是占位对象，`.value` 才是实际类）；无法解包时为 None，按 FastAPI 默认
    JSONResponse 处理。
    """

    path: str
    methods: frozenset
    dep_funcs: frozenset
    response_model: object | None
    response_class: type | None
    include_in_schema: bool
    is_stream: bool

    def returns_json(self) -> bool:
        """是否为 JSON 响应（含 FastAPI 默认 JSONResponse；流式/显式非 JSON 类不算）。"""
        if self.is_stream:
            return False
        if self.response_class is None:
            return True
        return issubclass(self.response_class, JSONResponse)


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


def _resolve_response_class(raw) -> type | None:
    """解包 fastapi 的 DefaultPlaceholder（duck-typing，不 import 私有类名）。"""
    if isinstance(raw, type):
        return raw
    value = getattr(raw, "value", None)
    return value if isinstance(value, type) else None


def _is_stream(node) -> bool:
    """JSONL / SSE 流式端点：response_model 对它们无意义，不计入 JSON 守门。"""
    return bool(
        getattr(node, "is_json_stream", False)
        or getattr(node, "is_sse_stream", False)
    )


def _scan(node, prefix: str = "", inherited_deps=frozenset()) -> list[RouteOperation]:
    """递归扫描路由树，返回 [RouteOperation]。

    按「携带 dependant 的节点即一个操作」判定，不用 isinstance(APIRoute)——
    0.141 的有效路由上下文不是 APIRoute 实例但 dependant 可用。未知形状直接
    raise：宁可炸得响亮，也不静默少扫（#306 的根因正是静默少扫）。
    """
    operations: list[RouteOperation] = []

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
            operations.append(RouteOperation(
                path=_join_prefix(node_prefix, ctx.path),
                methods=frozenset(ctx.methods or ()),
                dep_funcs=frozenset(
                    _collect_dep_funcs(ctx.dependant)
                    | node_deps
                    | _funcs_from_depends(getattr(ctx, "dependencies", None))
                ),
                response_model=getattr(ctx, "response_model", None),
                response_class=_resolve_response_class(
                    getattr(ctx, "response_class", None)
                ),
                include_in_schema=bool(getattr(ctx, "include_in_schema", True)),
                is_stream=_is_stream(ctx),
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
        operations.append(RouteOperation(
            path=full_path,
            methods=frozenset(node.methods or ()),
            dep_funcs=frozenset(_collect_dep_funcs(dependant) | inherited_deps),
            response_model=getattr(node, "response_model", None),
            response_class=_resolve_response_class(
                getattr(node, "response_class", None)
            ),
            include_in_schema=bool(getattr(node, "include_in_schema", True)),
            is_stream=_is_stream(node),
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
    return operations  # /docs、/openapi.json 等非业务路径不参与扫描


def scan_operations(app) -> list[RouteOperation]:
    """全量扫描 app 路由树，返回全部操作（含 `/`、`/health` 等非 /api 端点）。"""
    app_deps = _funcs_from_depends(getattr(app, "dependencies", None))
    operations: list[RouteOperation] = []
    for node in app.routes:
        operations.extend(_scan(node, inherited_deps=frozenset(app_deps)))
    return operations


def api_operations(app) -> list[RouteOperation]:
    """全量扫描 app 路由树，只保留 /api/* 操作。"""
    return [op for op in scan_operations(app) if op.path.startswith("/api/")]
