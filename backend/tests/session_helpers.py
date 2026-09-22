# ============================================================================
# 自持会话入口的测试替身 (session_helpers.py)
# ============================================================================
# 溯源：issue #592 方案 A——任务投递入口（submit_price_sync_job /
#       submit_snapshot_recalc_job / recover_orphan_jobs）不再接受注入会话，
#       改为自持 SessionLocal。
# 现状：app.database.SessionLocal 绑定 pytest 测试引擎，但工作在其**独立事务**里，
#       真实 commit 会逃逸 test_db 的 SAVEPOINT 回滚、泄漏到后续用例（单 active 锁
#       对残留 pending/running 行极敏感）。行为用例一律用
#       patch_non_closing_session_local 把自持会话重定向回 test_db——close() 归零、
#       commit() 原样，与 #592 之前的注入形态完全同构的隔离语义；
#       仅「跨事务可见性」用例故意不补丁、用真实 SessionLocal 并自行清理所造行。
# ============================================================================


class NonClosingSession:
    """代理注入会话，仅把 close() 归零。

    自持会话的 finally 里 close() 的是这个包装，test_db 本体不被关闭；
    commit/rollback/query/add/refresh 全部原样转发，保持 SAVEPOINT 隔离语义。
    """

    def __init__(self, session):
        self._session = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._session, name)


def patch_non_closing_session_local(monkeypatch, test_db):
    """让函数内 `from app.database import SessionLocal` 拿到 test_db 的非关闭包装。

    submit_* 的 SessionLocal 是调用期局部导入，monkeypatch 模块属性即可命中。
    """
    monkeypatch.setattr(
        "app.database.SessionLocal", lambda: NonClosingSession(test_db)
    )
