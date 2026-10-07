"""router 层错误文案 locale 契约（issue #680）

前端 `src/lib/api/client.ts` 的 `detailMessage` 对裸字符串 detail **原样透传**
（刻意不建码→文案映射表），所以这里断言的文本就是界面上那句话说到底的样子。
断言取**整体响应体相等**而非「状态码 + 中文子串」：子串式不再描述实际契约
（文案补一个句号都不会红），而 #679 的教训是「旧断言与新文案并存会证明一个
已不存在的契约」。带标识符的站点必须整体构造期望串，否则「标识符是否回显」
无人看守。

刻意未覆盖的三处（是可达性事实，不是遗漏）：
  - `trades.py:174` / `trades.py:237`：交易在、其 `(code, market)` 产品行不在。
    测试库外键全为 RESTRICT，造不出该形态；不为覆盖率去关 FOREIGN_KEY_CHECKS。
  - `products.py:156`：bogus code 先在 `resolve_product_market` 抛结构化
    `PRODUCT_NOT_FOUND`，本行是同事务内的防御分支，HTTP 打不到。
"""

import pytest

BOGUS_ID = 999999
BOGUS_CODE = "NO_SUCH_CODE"
BOGUS_PORTFOLIO = "NO_SUCH_PORT"

# (method, url, expected detail, json body)
LOCALE_CASES = [
    # ---------------- trades.py：7 处「交易不存在」 ----------------
    ("get", f"/api/trades/{BOGUS_ID}/preview", "交易不存在", None),
    ("get", f"/api/trades/{BOGUS_ID}", "交易不存在", None),
    ("post", f"/api/trades/{BOGUS_ID}/confirm", "交易不存在", None),
    ("post", f"/api/trades/{BOGUS_ID}/cancel", "交易不存在", None),
    ("post", f"/api/trades/{BOGUS_ID}/unconfirm", "交易不存在", None),
    ("put", f"/api/trades/{BOGUS_ID}", "交易不存在", {}),
    ("delete", f"/api/trades/{BOGUS_ID}", "交易不存在", None),
    # ---------------- subscriptions.py：7 处「申赎记录不存在」 ----------------
    ("get", f"/api/subscriptions/{BOGUS_ID}/preview", "申赎记录不存在", None),
    ("get", f"/api/subscriptions/{BOGUS_ID}", "申赎记录不存在", None),
    ("post", f"/api/subscriptions/{BOGUS_ID}/confirm", "申赎记录不存在", None),
    ("post", f"/api/subscriptions/{BOGUS_ID}/cancel", "申赎记录不存在", None),
    ("post", f"/api/subscriptions/{BOGUS_ID}/unconfirm", "申赎记录不存在", None),
    ("put", f"/api/subscriptions/{BOGUS_ID}", "申赎记录不存在", {}),
    ("delete", f"/api/subscriptions/{BOGUS_ID}", "申赎记录不存在", None),
    # ------------- share_change_events.py：7 处「份额变动事件不存在」 -------------
    ("get", f"/api/share-change-events/{BOGUS_ID}/preview", "份额变动事件不存在", None),
    ("get", f"/api/share-change-events/{BOGUS_ID}", "份额变动事件不存在", None),
    ("post", f"/api/share-change-events/{BOGUS_ID}/confirm", "份额变动事件不存在", None),
    ("post", f"/api/share-change-events/{BOGUS_ID}/cancel", "份额变动事件不存在", None),
    ("post", f"/api/share-change-events/{BOGUS_ID}/unconfirm", "份额变动事件不存在", None),
    ("put", f"/api/share-change-events/{BOGUS_ID}", "份额变动事件不存在", {}),
    ("delete", f"/api/share-change-events/{BOGUS_ID}", "份额变动事件不存在", None),
    # ------------- tasks.py：4 处「任务 {code} 不存在」（回显用户输入的码） -------------
    ("post", f"/api/system/tasks/{BOGUS_CODE}/run", f"任务 {BOGUS_CODE} 不存在", None),
    ("post", f"/api/system/tasks/{BOGUS_CODE}/enable", f"任务 {BOGUS_CODE} 不存在", None),
    ("post", f"/api/system/tasks/{BOGUS_CODE}/disable", f"任务 {BOGUS_CODE} 不存在", None),
    ("get", f"/api/system/tasks/{BOGUS_CODE}", f"任务 {BOGUS_CODE} 不存在", None),
    # ------------- positions.py：1 处短形 + 3 处回显组合码 -------------
    ("get", f"/api/positions/{BOGUS_ID}", "持仓记录不存在", None),
    (
        "get",
        f"/api/positions/portfolio/{BOGUS_PORTFOLIO}/available-cash",
        f"组合 {BOGUS_PORTFOLIO} 不存在",
        None,
    ),
    (
        "get",
        f"/api/positions/portfolio/{BOGUS_PORTFOLIO}/product/510300.SH/available-shares",
        f"组合 {BOGUS_PORTFOLIO} 不存在",
        None,
    ),
    (
        "get",
        f"/api/positions/portfolio/{BOGUS_PORTFOLIO}/investor/ADMIN/available-shares",
        f"组合 {BOGUS_PORTFOLIO} 不存在",
        None,
    ),
    # ------------- platforms.py：3 处回显平台码 -------------
    ("get", f"/api/platforms/{BOGUS_CODE}", f"平台 {BOGUS_CODE} 不存在", None),
    ("put", f"/api/platforms/{BOGUS_CODE}", f"平台 {BOGUS_CODE} 不存在", {}),
    ("delete", f"/api/platforms/{BOGUS_CODE}", f"平台 {BOGUS_CODE} 不存在", None),
    # ------------- products.py：2 处回显 (code, market) 复合键 -------------
    (
        "get",
        f"/api/products/{BOGUS_CODE}/CN_OTC",
        f"产品 {BOGUS_CODE}(CN_OTC) 不存在",
        None,
    ),
    (
        "delete",
        f"/api/products/{BOGUS_CODE}/CN_OTC",
        f"产品 {BOGUS_CODE}(CN_OTC) 不存在",
        None,
    ),
    # ------------- notifications.py：1 处短形 -------------
    ("post", f"/api/system/notifications/{BOGUS_ID}/read", "通知不存在", None),
    # ------------- portfolios.py / investors.py：回显业务码 -------------
    ("get", f"/api/portfolios/{BOGUS_CODE}", f"组合 {BOGUS_CODE} 不存在", None),
    ("get", f"/api/investors/{BOGUS_CODE}", f"投资人 {BOGUS_CODE} 不存在", None),
]


@pytest.mark.parametrize("method,url,detail,body", LOCALE_CASES)
def test_not_found_detail_is_chinese(client, admin_headers, method, url, detail, body):
    """45 处收尾后的文案本体：整体响应体相等，且必须是中文（无 ASCII 单词漏网）"""
    resp = client.request(method.upper(), url, json=body, headers=admin_headers)

    assert resp.status_code == 404
    assert resp.json() == {"detail": detail}


def test_investor_under_existing_portfolio(client, admin_headers, sample_portfolio):
    """positions.py:387：组合存在、投资人码不存在 → 回显投资人码而非组合码"""
    resp = client.get(
        f"/api/positions/portfolio/{sample_portfolio.code}/investor/{BOGUS_CODE}/available-shares",
        headers=admin_headers,
    )

    assert resp.status_code == 404
    assert resp.json() == {"detail": f"投资人 {BOGUS_CODE} 不存在"}


def test_platform_duplicate_returns_400_with_code(
    client, admin_headers, sample_platform
):
    """platforms.py:45：创建类 400 必须回显刚提交的自然键（对齐 ALREADY_EXISTS 口径）"""
    resp = client.post(
        "/api/platforms",
        json={"code": sample_platform.code, "name": "重复平台"},
        headers=admin_headers,
    )

    assert resp.status_code == 400
    assert resp.json() == {"detail": f"平台 {sample_platform.code} 已存在"}


def test_subscription_of_someone_else_returns_403(
    client, viewer_headers, test_db, sample_portfolio
):
    """subscriptions.py:127：资源归属门，措辞不得写成角色门槛「需要管理员权限」"""
    from tests.factories import create_subscription

    sub = create_subscription(
        test_db, portfolio_code=sample_portfolio.code, investor_code="ADMIN"
    )

    resp = client.get(f"/api/subscriptions/{sub.id}", headers=viewer_headers)

    assert resp.status_code == 403
    assert resp.json() == {"detail": "无权查看该申赎记录"}


def test_notification_of_someone_else_returns_403(client, viewer_headers, test_db):
    """notifications.py:55：同上，与 subscriptions 的 403 共用「无权…该 X」一种说法"""
    from tests.factories import create_notification

    notification = create_notification(test_db, recipient="ADMIN")

    resp = client.post(
        f"/api/system/notifications/{notification.id}/read", headers=viewer_headers
    )

    assert resp.status_code == 403
    assert resp.json() == {"detail": "无权操作该通知"}
