# ============================================================================
# 集成测试：系统类列表端点响应契约 (test_system_logs.py)
# ============================================================================
# issue #512/#513 接线的功能冒烟。以下 5 个列表端点此前未声明 response_model
# （ORM 行整行直吐），接线后用精确字段集断言锁住「响应字段集 == 响应模型字段集」：
# - GET /api/system/logs/login|audit|error（schemas/log.py 三个 Paginated*）
# - GET /api/system/tasks（PaginatedTaskResponse，同文件已有 PaginatedTaskLogResponse 先例）
# - GET /api/system/notifications（PaginatedNotificationResponse）
#
# 为什么断言精确集合而非子集：子集断言对「多吐/漏字段」不敏感，而这恰是本组改动
# 要锁的口径（#487 的 ORM 泄漏就是多吐）。筛选/排序/分页语义由各领域既有测试覆盖，
# 本文件只管契约面（信封形状 + 元素字段集）。
# ============================================================================

import pytest

from app.models.audit_log import AuditLog
from app.models.login_log import LoginLog
from app.models.notification import Notification
from app.models.scheduled_task import ScheduledTask
from app.models.system_error_log import SystemErrorLog
from app.schemas.log import (
    AuditLogResponse,
    LoginLogResponse,
    PaginatedAuditLogResponse,
    PaginatedLoginLogResponse,
    PaginatedSystemErrorLogResponse,
    SystemErrorLogResponse,
)
from app.schemas.notification import (
    NotificationResponse,
    PaginatedNotificationResponse,
)
from app.schemas.task import PaginatedTaskResponse, TaskResponse


def _assert_pagination_contract(payload: dict, paginated_model, item_model) -> None:
    """分页信封与元素字段集都精确等于响应模型定义（收窄口径的可观测证据）"""
    assert set(payload) == set(paginated_model.model_fields)
    assert isinstance(payload["items"], list)
    for item in payload["items"]:
        assert set(item) == set(item_model.model_fields)


class TestSystemLogEndpoints:
    """GET /api/system/logs/{login,audit,error}（#512 同型三处）"""

    def test_login_logs_contract(self, client, admin_headers, test_db):
        row = LoginLog(
            investor_code="LOG_I1",
            action="login",
            status="success",
            ip_address="10.0.0.1",
            user_agent="pytest",
        )
        test_db.add(row)
        test_db.commit()
        test_db.refresh(row)

        resp = client.get("/api/system/logs/login", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        _assert_pagination_contract(body, PaginatedLoginLogResponse, LoginLogResponse)
        assert body["total"] == 1
        assert body["items"][0]["id"] == row.id
        assert body["items"][0]["investor_code"] == "LOG_I1"
        assert body["items"][0]["failure_reason"] is None

    def test_audit_logs_contract(self, client, admin_headers, test_db):
        row = AuditLog(
            investor_code="ADMIN",
            action="create",
            resource_type="subscription",
            resource_id="1",
            resource_name="申赎 1",
            new_value='{"status": "pending"}',
        )
        test_db.add(row)
        test_db.commit()
        test_db.refresh(row)

        resp = client.get("/api/system/logs/audit", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        _assert_pagination_contract(body, PaginatedAuditLogResponse, AuditLogResponse)
        assert body["total"] == 1
        assert body["items"][0]["id"] == row.id
        assert body["items"][0]["resource_name"] == "申赎 1"

    def test_error_logs_contract(self, client, admin_headers, test_db):
        row = SystemErrorLog(
            error_type="unhandled_exception",
            error_message="boom",
            request_path="/api/positions",
            request_method="GET",
        )
        test_db.add(row)
        test_db.commit()
        test_db.refresh(row)

        # page_size 放大：本表有 test_db 事务之外的写入点——全局异常 handler 经
        # record_system_error 用独立 SessionLocal 提交（如 test_request_context.py 刻意
        # 触发的 500），残留行不随本用例回滚，故不断言 total/首位，只断言本行在集内。
        resp = client.get(
            "/api/system/logs/error", headers=admin_headers, params={"page_size": 100}
        )

        assert resp.status_code == 200
        body = resp.json()
        _assert_pagination_contract(
            body, PaginatedSystemErrorLogResponse, SystemErrorLogResponse
        )
        assert any(
            item["id"] == row.id and item["error_message"] == "boom"
            for item in body["items"]
        )

    @pytest.mark.parametrize(
        "path",
        ["/api/system/logs/login", "/api/system/logs/audit", "/api/system/logs/error"],
    )
    def test_viewer_forbidden(self, client, viewer_headers, path):
        """日志端点仅 admin（接线不改权限口径）"""
        assert client.get(path, headers=viewer_headers).status_code == 403


class TestTasksListEndpoint:
    """GET /api/system/tasks（#512）"""

    def test_tasks_list_contract(self, client, admin_headers, test_db):
        test_db.query(ScheduledTask).delete()
        test_db.add(
            ScheduledTask(
                code="CONTRACT_TASK",
                name="契约任务",
                description="列表契约用任务",
                cron_expr="0 7 * * 1-5",
            )
        )
        test_db.commit()

        resp = client.get("/api/system/tasks", headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()
        _assert_pagination_contract(body, PaginatedTaskResponse, TaskResponse)
        assert body["total"] == 1
        assert body["items"][0]["code"] == "CONTRACT_TASK"
        assert body["items"][0]["is_enabled"] is True


class TestNotificationsListEndpoint:
    """GET /api/system/notifications（#512）"""

    def test_notifications_contract_and_recipient_scope(
        self, client, admin_headers, viewer_headers, test_db
    ):
        test_db.query(Notification).delete()
        test_db.add_all(
            [
                Notification(type="system", title="全局通知"),
                Notification(type="system", title="发给 viewer", recipient="VIEWER"),
                Notification(type="system", title="发给他人", recipient="OTHER_I"),
            ]
        )
        test_db.commit()

        admin_resp = client.get("/api/system/notifications", headers=admin_headers)
        assert admin_resp.status_code == 200
        admin_body = admin_resp.json()
        _assert_pagination_contract(
            admin_body, PaginatedNotificationResponse, NotificationResponse
        )
        assert admin_body["total"] == 3

        viewer_resp = client.get("/api/system/notifications", headers=viewer_headers)
        assert viewer_resp.status_code == 200
        viewer_body = viewer_resp.json()
        _assert_pagination_contract(
            viewer_body, PaginatedNotificationResponse, NotificationResponse
        )
        assert viewer_body["total"] == 2
        assert {i["title"] for i in viewer_body["items"]} == {"全局通知", "发给 viewer"}
