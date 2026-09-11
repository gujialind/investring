# ============================================================================
# 单元测试：任务执行记录归一口径（issue #406）
# ============================================================================
# 覆盖 `task_runner` 内部的两张表：
# - `_TASK_DISPATCH`：任务码 → 执行体。新增任务只加 dispatch 而漏定 records 口径
#   时，`run_task` 会在 `_derive_log_fields` 抛 ValueError，本测试把该约束固化。
# - `_derive_log_fields`：各任务返回 dict → (status, total, success, failed, error_message)。
#   重点守 #305 语义（snapshot_generate 的 warnings / auto_confirm_failed 归并 +
#   1000 字符截断）与「failed 为 None 表示未度量、不是零失败」的口径。
# 纯函数测试，无需 DB。
# ============================================================================

import pytest

from app.services.exceptions import NotFoundError
from app.services.task_runner import (
    ERROR_MESSAGE_MAX,
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    _TASK_DISPATCH,
    _derive_log_fields,
    run_task,
)


class TestTriggerTypeConstants:
    """trigger_type 取值口径（列宽 String(20)）"""

    def test_values_fit_column_width(self):
        assert (TRIGGER_MANUAL, TRIGGER_SCHEDULED) == ("manual", "scheduled")
        assert all(len(v) <= 20 for v in (TRIGGER_MANUAL, TRIGGER_SCHEDULED))


class TestDispatchCoverage:
    """派发表与归一口径必须同时覆盖四个任务码"""

    def test_all_task_codes_dispatched(self):
        assert set(_TASK_DISPATCH) == {
            "nav_sync", "snapshot_generate", "trading_calendar_sync", "log_cleanup",
        }

    def test_every_dispatched_code_has_record_semantics(self):
        """每个能被执行的任务码都必须能归一，不得抛「未定义口径」"""
        dummy = {
            "products_count": 0, "failed_products": [],
            "portfolios_processed": 0, "warnings": [], "auto_confirm_failed": [],
            "synced_count": 0, "login_logs": 0,
        }
        for code in _TASK_DISPATCH:
            status, *_ = _derive_log_fields(code, dummy)
            assert status  # 不抛异常即达标

    def test_unknown_code_raises_value_error(self):
        with pytest.raises(ValueError, match="未定义任务执行记录口径"):
            _derive_log_fields("no_such_task", {})


class TestUnknownTaskCode:
    """未知任务码：抛 NotFoundError，且不建执行记录"""

    def test_unknown_code_raises_not_found(self, test_db):
        from app.models.task_execution_log import TaskExecutionLog

        with pytest.raises(NotFoundError) as exc:
            run_task(test_db, "no_such_task")

        assert exc.value.code == "TASK_NOT_FOUND"
        assert exc.value.http_status == 404
        assert "no_such_task" in exc.value.message
        assert exc.value.details["available_tasks"] == sorted(_TASK_DISPATCH)
        # 没执行过的任务不该留下执行记录
        assert test_db.query(TaskExecutionLog).count() == 0


class TestNavSyncDerivation:
    """nav_sync：total = products_count、failed = len(failed_products)、success = 差额"""

    def test_all_success(self):
        status, total, success, failed, message = _derive_log_fields("nav_sync", {
            "synced_count": 42, "products_count": 9, "failed_products": [],
        })
        assert (status, total, success, failed, message) == ("success", 9, 9, 0, None)

    def test_partial_failure_is_self_consistent(self):
        status, total, success, failed, message = _derive_log_fields("nav_sync", {
            "synced_count": 30, "products_count": 9, "failed_products": ["A.OF", "B.OF"],
        })
        assert status == "partial_success"
        assert (total, success, failed) == (9, 7, 2)
        assert total == success + failed  # 验收断言：三者自洽
        assert message is None  # 现状语义：nav_sync 不写 error_message

    def test_missing_keys_degrade_to_zero_without_crash(self):
        status, total, success, failed, _ = _derive_log_fields("nav_sync", {})
        assert (status, total, success, failed) == ("success", 0, 0, 0)


class TestSnapshotGenerateDerivation:
    """snapshot_generate：#305 语义 + records 归一口径"""

    def test_clean_run(self):
        status, total, success, failed, message = _derive_log_fields("snapshot_generate", {
            "snapshots_generated": 5, "portfolios_processed": 2,
            "warnings": [], "auto_confirm_failed": [],
        })
        assert (status, total, success, failed, message) == ("success", 2, 2, 0, None)

    def test_warnings_only_keeps_success_status(self):
        """仅有 warnings → status=success，error_message 以 warnings: 开头"""
        status, total, success, failed, message = _derive_log_fields("snapshot_generate", {
            "portfolios_processed": 1, "warnings": [{"type": "negative_cash"}],
            "auto_confirm_failed": [],
        })
        assert status == "success"
        assert message == "warnings: negative_cash"
        assert (total, success, failed) == (1, 1, 0)

    def test_auto_confirm_failed_is_partial_success_with_codes(self):
        """#305：有 auto_confirm_failed → partial_success，error_message 含逐条 code: error"""
        status, total, success, failed, message = _derive_log_fields("snapshot_generate", {
            "portfolios_processed": 3,
            "warnings": [],
            "auto_confirm_failed": [
                {"code": "MISSING_NAV", "error": "缺净值"},
                {"code": "NEGATIVE_CASH", "error": "负现金"},
            ],
        })
        assert status == "partial_success"
        assert (total, success, failed) == (3, 1, 2)
        assert message == "MISSING_NAV: 缺净值; NEGATIVE_CASH: 负现金"

    def test_failed_and_warnings_are_joined(self):
        """两者并存时 warnings 追加在同一行（原 router 分支语义）"""
        status, _, _, _, message = _derive_log_fields("snapshot_generate", {
            "portfolios_processed": 1, "warnings": [{"type": "negative_cash"}],
            "auto_confirm_failed": [{"code": "MISSING_NAV", "error": "缺净值"}],
        })
        assert status == "partial_success"
        assert message == "MISSING_NAV: 缺净值 | warnings: negative_cash"

    def test_missing_keys_in_failed_items_use_placeholders(self):
        status, _, _, _, message = _derive_log_fields("snapshot_generate", {
            "portfolios_processed": 1, "warnings": [{}], "auto_confirm_failed": [{}],
        })
        assert status == "partial_success"
        assert message == "UNKNOWN:  | warnings: unknown"

    def test_error_message_truncated_to_1000(self):
        """沿用 #305 的 1000 字符截断（列是 Text，但读侧契约按此口径）"""
        many = [{"code": "MISSING_NAV", "error": "x" * 100} for _ in range(50)]
        _, _, _, _, message = _derive_log_fields("snapshot_generate", {
            "portfolios_processed": 1, "warnings": [], "auto_confirm_failed": many,
        })
        assert len(message) == ERROR_MESSAGE_MAX == 1000

    def test_warnings_only_message_truncated_to_1000(self):
        many = [{"type": "t" * 50} for _ in range(50)]
        _, _, _, _, message = _derive_log_fields("snapshot_generate", {
            "portfolios_processed": 1, "warnings": many, "auto_confirm_failed": [],
        })
        assert message.startswith("warnings: ")
        assert len(message) == ERROR_MESSAGE_MAX

    def test_negative_total_guard(self):
        """分组数缺失时降级为 0，不产生负的 success（读数会误导排查）"""
        status, total, success, failed, _ = _derive_log_fields("snapshot_generate", {
            "warnings": [], "auto_confirm_failed": [],
        })
        assert (status, total, success, failed) == ("success", 0, 0, 0)


class TestOtherTaskDerivation:
    """trading_calendar_sync / log_cleanup：failed 留 None（未度量）"""

    def test_calendar_sync_counts_synced_days(self):
        status, total, success, failed, message = _derive_log_fields(
            "trading_calendar_sync", {"synced_count": 365, "year": 2026}
        )
        assert (status, total, success, failed, message) == ("success", 365, 365, None, None)

    def test_log_cleanup_sums_deleted_rows(self):
        status, total, success, failed, message = _derive_log_fields("log_cleanup", {
            "login_logs": 3, "audit_logs": 4, "nav_sync_details": 5,
            "task_logs": 2, "error_logs": 1,
        })
        # 5 类删除量求和 = 15；failed 未度量故为 None，不是 0
        assert (status, total, success, failed, message) == ("success", 15, 15, None, None)

    def test_log_cleanup_ignores_non_int_values(self):
        _, total, success, _, _ = _derive_log_fields(
            "log_cleanup", {"login_logs": 2, "note": "oops"}
        )
        assert (total, success) == (2, 2)
