"""API Key 的显式端点白名单；新增路由不自动获得代理访问权。"""

READ_PATHS = frozenset({
    "/api/auth/status",
    "/api/investors", "/api/investors/{code}",
    "/api/portfolios", "/api/portfolios/{code}",
    "/api/portfolios/{code}/nav-history", "/api/portfolios/{code}/snapshots/latest",
    "/api/portfolios/{code}/investors", "/api/portfolios/{code}/returns",
    "/api/portfolios/{code}/performance", "/api/portfolios/{code}/cash-flow",
    "/api/portfolios/{portfolio_code}/cash-transfers",
    "/api/portfolios/{portfolio_code}/cash-flows",
    "/api/products", "/api/products/{code}", "/api/products/{code}/{market}",
    "/api/asset-classifications", "/api/asset-classifications/{code}",
    "/api/platforms", "/api/platforms/{code}",
    "/api/trading-calendar", "/api/trading-calendar/next",
    "/api/trading-calendar/prev", "/api/trading-calendar/is-open",
    "/api/market-data/products/{code}/{market}/price-data",
    "/api/market-data/products/{code}/{market}/nav-history",
    "/api/market-data/products/{code}/{market}/nav-analysis",
    "/api/market-data/products/{code}/{market}/nav-coverage",
    "/api/trades", "/api/trades/{id}", "/api/trades/{id}/preview",
    "/api/subscriptions", "/api/subscriptions/{id}", "/api/subscriptions/{id}/preview",
    "/api/share-change-events", "/api/share-change-events/{id}",
    "/api/share-change-events/{id}/preview",
    "/api/positions", "/api/positions/{id}",
    "/api/positions/portfolio/{portfolio_code}/holdings/by-product",
    "/api/positions/portfolio/{portfolio_code}/holdings/by-platform",
    "/api/positions/portfolio/{portfolio_code}/available-cash",
    "/api/positions/portfolio/{portfolio_code}/product/{product_code}/available-shares",
    "/api/positions/portfolio/{portfolio_code}/investor/{investor_code}/available-shares",
    "/api/positions/portfolio/{portfolio_code}/cash-position",
    "/api/snapshots/validation", "/api/snapshots/portfolios/{code}/status",
    "/api/snapshots/portfolios/{code}/list",
})

WRITE_OPERATIONS = frozenset({
    *(("POST", f"/api/{resource}{suffix}")
      for resource in ("trades", "subscriptions", "share-change-events")
      for suffix in ("", "/{id}/confirm", "/{id}/cancel", "/{id}/unconfirm")),
    *(("PUT", f"/api/{resource}/{{id}}")
      for resource in ("trades", "subscriptions", "share-change-events")),
    ("POST", "/api/portfolios/{portfolio_code}/cash-transfer"),
    ("POST", "/api/portfolios/{portfolio_code}/cash-transfer/{transfer_group}/confirm"),
    ("POST", "/api/portfolios/{portfolio_code}/cash-transfer/{transfer_group}/cancel"),
})


def key_allows(scopes: list[str], method: str, route_path: str) -> bool:
    if method == "GET" and route_path in READ_PATHS:
        return "ledger:read" in scopes or "ledger:write" in scopes
    return "ledger:write" in scopes and (method, route_path) in WRITE_OPERATIONS
