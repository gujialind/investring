# ============================================================================
# 路由兜底的观测出口自检 (test_error_reporting.py)
# ============================================================================
# 为什么单独成文件：router 里「`except Exception` → 抛 HTTPException(5xx)」这一族写法，
# 会让 `main.py` 的全局 Exception handler **永远收不到**——`HTTPException` 由
# Starlette 的 ExceptionHandlerMiddleware（中间件链内侧）就地渲染成响应，冒不到装着
# Exception handler 的 ServerErrorMiddleware（最外层）。原始异常类型与堆栈就此丢失：
# stdout 无 ERROR 行、`system_error_log` 不落一行，事后只剩响应体里 `str(e)` 一句话。
# `app/error_reporting.py` 是这些分支的统一出口（issue #553），本文件钉住出口自身的
# 契约：error_type 是「因」不是「果」、有 ERROR 行且有堆栈、best-effort 落库失败不许
# 吞掉真因。
#
# 「哪些兜底分支接上了、漏一处就红」不在这里——那份 AST 守门在
# [test_catchall_logging_guard.py](test_catchall_logging_guard.py)：它按「catch-all →
# 5xx」判红、带下限棘轮与反例，比「扫到 catch-all 就要求接出口」更贴 #553 的判据。
# 行为侧（真落库、反向用例）见
# [test_router_catchall_observability.py](../integration/test_router_catchall_observability.py)。
#
# 诚实边界：本文件不验证「stdout 真的刷到了日志收集端」，也不重跑 10 个端点的 500
# 路径（那部分由各自端点的集成用例覆盖）。
# ============================================================================

import logging

import pytest

import app.error_reporting as er


class TestReportUnexpected:
    def test_error_type_is_the_original_exception(self, monkeypatch):
        """#553 的根因：error_type 若落成 HTTPException，「真因是什么」就没有答案"""
        captured = {}
        monkeypatch.setattr(er, "record_system_error", lambda **kw: captured.update(kw))

        try:
            raise TypeError("快照单价是 None")
        except TypeError as e:
            er.report_unexpected(e, operation="generate_snapshot")

        assert captured["error_type"] == "TypeError"
        assert captured["error_message"] == "快照单价是 None"
        assert "TypeError" in captured["error_stack"]

    def test_logs_error_with_traceback_and_operation(self, caplog, monkeypatch):
        """出口要同时满足「有 ERROR 行」与「有堆栈」，两者缺一都无法事后定位

        落库侧替换为空实现：本用例只断言日志，而 `record_system_error` 走独立 session
        提交，不受任何事务回滚保护——真落一行就会留在测试库里，让 test_log_cleanup
        那类按整表计数的断言失准（跨用例污染，#553 引入后实测）。
        """
        monkeypatch.setattr(er, "record_system_error", lambda **_kw: None)
        try:
            raise ValueError("boom")
        except ValueError as e:
            with caplog.at_level(logging.ERROR, logger="app.error_reporting"):
                er.report_unexpected(e, operation="recalculate")

        records = [r for r in caplog.records if r.name == "app.error_reporting"]
        assert records, "没有 ERROR 日志出口"
        record = records[-1]
        assert record.levelno == logging.ERROR
        # exc_info=exc 而非 logger.exception：后者非 except 块里调用会静默记成 NoneType
        assert record.exc_info is not None and record.exc_info[0] is ValueError
        assert getattr(record, "operation") == "recalculate"

    def test_record_failure_does_not_mask_original(self, monkeypatch):
        """落 system_error_log 是 best-effort，但它自己炸了不许把真因吞掉"""
        monkeypatch.setattr(
            er, "record_system_error", lambda **_kw: (_ for _ in ()).throw(
                RuntimeError("system_error_log 写不进去")
            ),
        )
        with pytest.raises(RuntimeError):
            try:
                raise TypeError("真因")
            except TypeError as e:
                er.report_unexpected(e, operation="run_task")
