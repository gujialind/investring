"""router 层 200 成功回执文案的 locale 契约（issue #687）

与 `test_router_error_locale.py`（#680）同族不同面：那份管 4xx 的 `detail`，本份管
200 响应体的 `message`。两份都不可省——它们各自钉住一条「后端写英文就等于界面/CLI
写英文」的口径，而消费路径不同：错误文案经前端 `client.ts` 的 `detailMessage` 原样
透传上 toast，成功回执**前端不消费**（各 hooks 的 `onSuccess` 忽略 payload、自写中文
toast），只有 `ir-cli` 的 `output.py::success()` 直出到终端，故 `ir trade cancel`
这类命令用户看到的实质内容就是这里的一句话。

断言取**整体响应体相等**而非子串（与 #680 同理由：子串式不再描述实际契约，文案补一个
句号都不会红）。仅 `.../confirm` 三处降级为断 `["message"]`——它们的响应体带
`id`/`portfolio_code`/`status`/`event` 等十余个业务键，整体相等会把本条文案契约扩成
对整个确认响应形状的契约，那是 `test_response_model_guard.py` 与各生命周期测试的职责。

**本文件存在的直接原因是增量覆盖率门禁，不只是加固**：#687 的 21 行改动里有 7 行的
成功路径此前无任何测试执行（`investors.py:91`、`notifications.py:61/78`、
`platforms.py:102`、`products.py:191`、`tasks.py:120/135`——#680 那份 locale 只打了
它们的 404/403 分支），实测 `diff-cover --fail-under=80` 在只改文案时为 **63%（7 行
missing）判红**。其余 14 行由既有生命周期测试执行但不断文本，本文件把它们的话也钉住。

造数硬约束（踩过就是全局污染）：业务行一律 function 级现造、用 `MSG687_` 前缀的独立
码，**不删 session 种子**（`_seed_base_data` 的 4 平台 / 10 产品 / ADMIN 被整个会话共用）；
`ScheduledTask` 在测试库不自动种子，本文件自行插入专用 `MSG687_TASK` 一行，不改任何
既有 task 的 `is_enabled`。

与 `ir-cli` 的分工：`--quiet` 投影按**键** `message` 取值（`commands/trades.py:16` 的
`QUIET_MESSAGE_FIELDS`），改值不改键故不受影响；若将来有人删掉这个键，判红的是
`ir-cli/tests/test_usage_contract.py:141`，不是本文件。
"""

from datetime import date
from decimal import Decimal

from app.models.scheduled_task import ScheduledTask
from tests.factories import (
    create_investor,
    create_notification,
    create_platform,
    create_portfolio,
    create_position_snapshot,
    create_product,
    create_share_change_event,
    create_trade,
    create_value_snapshot,
    ensure_trading_day,
)

TRADE_DAYS = (date(2025, 10, 3), date(2025, 10, 6), date(2025, 10, 7))


def _seed_cash(test_db, portfolio_code, platform_code, amount=50000.0):
    """买入要验资：铺一笔已确认的 CASH 入账腿（#493 的既成事实口径）"""
    create_trade(
        test_db, portfolio_code, "CASH", "",
        trade_type="buy", amount=amount, price=None,
        platform_code=platform_code, trade_date=date(2025, 10, 3),
        confirm_date=date(2025, 10, 3), status="confirmed",
    )


class TestTradeReceiptMessages:
    """trades.py:252/273/287/322"""

    def _make_pending_exchange_trade(self, client, admin_headers, code):
        return client.post(
            "/api/trades",
            json={
                "portfolio_code": code,
                "product_code": "MSG687_ETF",
                "market": "CN_EXCHANGE",
                "trade_type": "buy",
                "amount": 10000.0,
                "price": 1.5,
                "platform_code": "MSG687_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        ).json()["id"]

    def _make_pending_otc_trade(self, client, admin_headers, amount):
        """场外 pending 买入（创建期不取价，取消/删除都不需要 PriceRecord）"""
        return client.post(
            "/api/trades",
            json={
                "portfolio_code": "MSG687_TP",
                "product_code": "MSG687_FUND",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": amount,
                "platform_code": "MSG687_PLAT",
                "trade_date": "2025-10-06",
            },
            headers=admin_headers,
        ).json()["id"]

    def test_confirm_unconfirm_cancel_and_delete(self, client, admin_headers, test_db):
        """确认 → 取消确认走场内一笔；取消与删除另造场外 pending

        刻意不在同一笔上串完四步：场内交易不可取消（`CANNOT_CANCEL_EXCHANGE`，
        生命周期规则要人改字段或删除重建），而取消要求 pending、删除拒绝 confirmed。
        """
        create_portfolio(test_db, code="MSG687_TP", status="active")
        create_platform(test_db, code="MSG687_PLAT")
        create_product(test_db, code="MSG687_ETF", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        create_product(test_db, code="MSG687_FUND", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1)
        for d in TRADE_DAYS:
            ensure_trading_day(test_db, d, is_open=True)
        _seed_cash(test_db, "MSG687_TP", "MSG687_PLAT")

        trade_id = self._make_pending_exchange_trade(client, admin_headers, "MSG687_TP")

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        # confirm 响应体带完整记录，只钉文案键（见文件头）
        assert conf.json()["message"] == "交易确认成功"

        unconf = client.post(f"/api/trades/{trade_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 200, f"Response: {unconf.status_code} {unconf.json()}"
        assert unconf.json() == {"message": "交易取消确认成功"}

        cancel_target = self._make_pending_otc_trade(client, admin_headers, 1000.0)
        cancel = client.post(f"/api/trades/{cancel_target}/cancel", headers=admin_headers)
        assert cancel.status_code == 200, f"Response: {cancel.status_code} {cancel.json()}"
        assert cancel.json() == {"message": "交易取消成功"}

        delete_target = self._make_pending_otc_trade(client, admin_headers, 1000.0)
        dele = client.delete(f"/api/trades/{delete_target}", headers=admin_headers)
        assert dele.status_code == 200, f"Response: {dele.status_code} {dele.json()}"
        assert dele.json() == {"message": "交易删除成功"}


class TestSubscriptionReceiptMessages:
    """subscriptions.py:148/173/189/223"""

    def _create_subscribe(self, client, admin_headers, code):
        return client.post(
            "/api/subscriptions",
            json={
                "portfolio_code": code,
                "investor_code": "MSG687_INV",
                "sub_type": "subscribe",
                "amount": 10000.0,
                "apply_date": "2025-09-01",
                "platform_code": "MSG687_SPLAT",
            },
            headers=admin_headers,
        )

    def test_confirm_unconfirm_cancel_and_delete(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="MSG687_SP", status="draft")
        create_investor(test_db, code="MSG687_INV")
        create_platform(test_db, code="MSG687_SPLAT")
        ensure_trading_day(test_db, date(2025, 9, 1), is_open=True)
        ensure_trading_day(test_db, date(2025, 9, 2), is_open=True)

        created = self._create_subscribe(client, admin_headers, "MSG687_SP")
        assert created.status_code == 200, f"Response: {created.status_code} {created.json()}"
        sub_id = created.json()["id"]

        conf = client.post(f"/api/subscriptions/{sub_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        assert conf.json()["message"] == "申赎记录确认成功"

        unconf = client.post(f"/api/subscriptions/{sub_id}/unconfirm", headers=admin_headers)
        assert unconf.status_code == 200, f"Response: {unconf.status_code} {unconf.json()}"
        assert unconf.json() == {"message": "申赎记录取消确认成功"}

        cancel = client.post(f"/api/subscriptions/{sub_id}/cancel", headers=admin_headers)
        assert cancel.status_code == 200, f"Response: {cancel.status_code} {cancel.json()}"
        assert cancel.json() == {"message": "申赎记录取消成功"}

        # confirmed 不可直接删，删除另起一笔 pending
        second = self._create_subscribe(client, admin_headers, "MSG687_SP")
        assert second.status_code == 200, f"Response: {second.status_code} {second.json()}"

        dele = client.delete(f"/api/subscriptions/{second.json()['id']}", headers=admin_headers)
        assert dele.status_code == 200, f"Response: {dele.status_code} {dele.json()}"
        assert dele.json() == {"message": "申赎记录删除成功"}


class TestShareEventReceiptMessages:
    """share_change_events.py:185/199/222/256"""

    ENT = date(2025, 12, 8)
    EX = date(2025, 12, 10)
    FUND = "MSG687_SEF"

    def _setup(self, test_db, portfolio_code, shares=1000.0):
        create_portfolio(test_db, code=portfolio_code, status="active")
        create_product(test_db, code=self.FUND, market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_platform(test_db, code="MSG687_SEPLAT")
        ensure_trading_day(test_db, self.ENT, is_open=True)
        ensure_trading_day(test_db, self.EX, is_open=True)
        create_position_snapshot(
            test_db, portfolio_code, self.FUND, "CN_OTC", snapshot_date=self.ENT,
            shares=shares, unit_price=1.0, cost_price=1.0,
            market_value=shares, platform_code="MSG687_SEPLAT",
        )
        create_value_snapshot(test_db, portfolio_code, self.ENT,
                              total_value=shares, total_shares=shares, unit_price=1.0)

    def _create(self, test_db, portfolio_code):
        return create_share_change_event(
            test_db, portfolio_code, self.FUND, "CN_OTC",
            event_type="reinvest_dividend", ex_date=self.EX, entitlement_date=self.ENT,
            status="pending", platform_code="MSG687_SEPLAT",
            div_cash=Decimal("0.0119"), reinvest_nav=Decimal("1.0899"),
        )

    def test_confirm_unconfirm_cancel_and_delete(self, client, admin_headers, test_db):
        self._setup(test_db, "MSG687_EP")
        event = self._create(test_db, "MSG687_EP")

        conf = client.post(
            f"/api/share-change-events/{event.id}/confirm", headers=admin_headers
        )
        assert conf.status_code == 200, f"Response: {conf.status_code} {conf.json()}"
        assert conf.json()["message"] == "份额变动事件确认成功"

        unconf = client.post(
            f"/api/share-change-events/{event.id}/unconfirm", headers=admin_headers
        )
        assert unconf.status_code == 200, f"Response: {unconf.status_code} {unconf.json()}"
        assert unconf.json()["message"] == "份额变动事件取消确认成功"

        cancel = client.post(
            f"/api/share-change-events/{event.id}/cancel", headers=admin_headers
        )
        assert cancel.status_code == 200, f"Response: {cancel.status_code} {cancel.json()}"
        assert cancel.json() == {"message": "份额变动事件取消成功"}

        # 删除走另一条 pending 事件（#214 生命周期守卫不属本条契约，不在此顺带验证）
        second = self._create(test_db, "MSG687_EP")
        dele = client.delete(
            f"/api/share-change-events/{second.id}", headers=admin_headers
        )
        assert dele.status_code == 200, f"Response: {dele.status_code} {dele.json()}"
        assert dele.json() == {"message": "份额变动事件删除成功"}


class TestPortfolioReceiptMessages:
    """portfolios.py:114/125——组合码是用户输入，按 #680 口径回显"""

    def test_close_then_reactivate(self, client, admin_headers, test_db):
        create_portfolio(test_db, code="MSG687_PC", status="active")

        closed = client.post("/api/portfolios/MSG687_PC/close", headers=admin_headers)
        assert closed.status_code == 200, f"Response: {closed.status_code} {closed.json()}"
        assert closed.json() == {"message": "组合 MSG687_PC 关闭成功"}

        reactivated = client.post(
            "/api/portfolios/MSG687_PC/reactivate", headers=admin_headers
        )
        assert reactivated.status_code == 200, (
            f"Response: {reactivated.status_code} {reactivated.json()}"
        )
        assert reactivated.json() == {"message": "组合 MSG687_PC 激活成功"}


class TestNotificationReceiptMessages:
    """notifications.py:61/78——admin 不受 recipient 归属过滤，但单条已读必须有真实行"""

    def test_mark_one_and_mark_all(self, client, admin_headers, test_db):
        notification = create_notification(test_db, recipient="ADMIN", status="pending")

        read = client.post(
            f"/api/system/notifications/{notification.id}/read", headers=admin_headers
        )
        assert read.status_code == 200, f"Response: {read.status_code} {read.json()}"
        assert read.json() == {"message": "通知已标记为已读"}

        create_notification(test_db, recipient="ADMIN", status="pending")

        all_read = client.post(
            "/api/system/notifications/read-all", headers=admin_headers
        )
        assert all_read.status_code == 200, f"Response: {all_read.status_code} {all_read.json()}"
        assert all_read.json() == {"message": "全部通知已标记为已读"}


class TestDeleteReceiptAndTaskToggleMessages:
    """products.py:191 / platforms.py:102 / investors.py:91 / tasks.py:120,135

    四处都是「键在、值没人断」的端点：业务码由用户输入，故回显（与 #680 的 404 分档
    一致）。删除对象一律现造的零关联行——外键全库 RESTRICT，删种子行会撞 IntegrityError。
    """

    def test_product_delete_echoes_code_and_market(self, client, admin_headers, test_db):
        create_product(test_db, code="MSG687_DEL_P", market="CN_OTC")

        resp = client.delete(
            "/api/products/MSG687_DEL_P/CN_OTC", headers=admin_headers
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json() == {"message": "产品 MSG687_DEL_P(CN_OTC) 删除成功"}

    def test_platform_delete_echoes_code(self, client, admin_headers, test_db):
        create_platform(test_db, code="MSG687_DEL_PLAT")

        resp = client.delete("/api/platforms/MSG687_DEL_PLAT", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json() == {"message": "平台 MSG687_DEL_PLAT 删除成功"}

    def test_investor_delete_echoes_code(self, client, admin_headers, test_db):
        create_investor(test_db, code="MSG687_DEL_INV")

        resp = client.delete("/api/investors/MSG687_DEL_INV", headers=admin_headers)
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json() == {"message": "投资人 MSG687_DEL_INV 删除成功"}

    def test_task_enable_disable_echoes_code(self, client, admin_headers, test_db):
        test_db.add(
            ScheduledTask(
                code="MSG687_TASK",
                name="文案契约专用任务",
                is_enabled=False,
                cron_expr="0 3 * * *",
            )
        )
        test_db.commit()

        enabled = client.post(
            "/api/system/tasks/MSG687_TASK/enable", headers=admin_headers
        )
        assert enabled.status_code == 200, f"Response: {enabled.status_code} {enabled.json()}"
        assert enabled.json() == {"message": "任务 MSG687_TASK 启用成功"}

        disabled = client.post(
            "/api/system/tasks/MSG687_TASK/disable", headers=admin_headers
        )
        assert disabled.status_code == 200, f"Response: {disabled.status_code} {disabled.json()}"
        assert disabled.json() == {"message": "任务 MSG687_TASK 禁用成功"}
