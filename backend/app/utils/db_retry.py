"""MySQL 死锁（errno 1213）的有界重试（issue #618）。

死锁是怎么来的（实测结论，不是推测；证据与对照实验见 issue #618）
------------------------------------------------------------------
快照生成走「先删后插」：`_delete_existing_snapshots` 先按
`(portfolio_code, snapshot_date)` 删三张快照表，再插入当日行。在 **REPEATABLE
READ** 下，那条**命中 0 行**的 DELETE 一样会在唯一索引上留下 gap lock
（next-key 锁的间隙部分）；而 gap 锁彼此是**兼容**的，于是两个并发事务可以同时
持有同一段间隙的 X gap 锁。随后各自的 INSERT 需要 insert intention lock，而它与
**对方**的 gap 锁互斥 → 循环等待 → InnoDB 选一方整体回滚并报 1213。

实测从 RDS 取到的 `LATEST DETECTED DEADLOCK` 两侧完全对称，都是「持有 supremum
上的 X gap 锁、等同一处的 insert intention 锁」：

    *** (1) HOLDS THE LOCK(S):
    RECORD LOCKS index uix_snapshot_portfolio_date ... lock_mode X
    Record lock, heap no 1 PHYSICAL RECORD: ... asc supremum;;
    *** (1) WAITING FOR THIS LOCK TO BE GRANTED:
    RECORD LOCKS index uix_snapshot_portfolio_date ... lock_mode X insert intention waiting
    Record lock, heap no 1 PHYSICAL RECORD: ... asc supremum;;
    *** (2) 同上，角色互换
    *** WE ROLL BACK TRANSACTION (2)

三条由对照实验测定（各 5 轮，2 事务用栅栏钉死交错）的关键事实：

1. **跨组合也会撞**。两个事务写的是**不同** `portfolio_code`，争的却是同一段索引
   间隙——不是同一行。所以「按组合加应用级互斥」治不了它：给两侧各套一把
   `GET_LOCK(ir_snapshot:<code>)`，5/5 仍然死锁（两把锁名不同，根本互不阻塞）。
2. **只在 REPEATABLE READ 下发生**。同样的写序列换成 READ-COMMITTED（生产 RDS 的
   实例级默认，`@@global.transaction_isolation` 实测为 READ-COMMITTED）5/5 不死锁
   ——RC 下 gap lock 基本关闭。CI 的 `mysql:8.4` 服务容器用 MySQL 默认 RR，所以
   这个缺陷在 CI 的 E2E 里显形、在生产不显形。
3. **gap lock 的来源就是那条 0 行 DELETE**。去掉 DELETE 只留 INSERT，RR 下 5/5
   不死锁；在索引里插一条字典序落在两个组合码之间的记录、把间隙切开，同样 5/5
   不死锁。这解释了它为什么是「偶发」而非必现：只有两个键落进**同一段**间隙时才撞。
   前端 E2E 恰好制造这个条件——`isolatedPortfolioCode` 生成的组合码形如
   `E2E493<日期><D|M><worker><retry><后缀>`，两个 worker 的组合码共享长前缀、
   在索引里紧邻，且首次生成快照前那段区间是空的（同一段间隙）。

为什么选「有界重试」
--------------------
* 1213 的语义本身就是「事务已被整体回滚，请重开事务」（MySQL 原文
  `try restarting transaction`），重试是官方处置；快照生成早有整体回滚语义，
  重放不会留下半截数据。实测 1 次重试即可吸收（5/5）。
* 「按组合互斥」被上面第 1 条实测否决。
* 「探针为空就跳过 DELETE」也能消掉这一例（实测 5/5 不死锁，且目标日已有旧行时
  照常删除、不退化成 1062 重复键），但它只覆盖「删 0 行」这一子集：间隙被别的
  路径锁住时仍会死锁，且它让写入依赖 RR 一致性读的可见性。相比之下重试对
  **整类** 1213 都成立，也不改任何数据访问语义。

刻意**不**重试 errno 1205（lock wait timeout）：那是「等锁超过
`innodb_lock_wait_timeout`」，重试只是把同样的等待再来一遍并加倍负载；真因是
长事务或锁范围过大，应当响亮失败而不是被重试盖住。
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# MySQL「Deadlock found when trying to get lock; try restarting transaction」。
# 只有这一个 errno 可重试，理由见模块 docstring 末段（1205 刻意排除）。
MYSQL_DEADLOCK = 1213

# 总尝试次数（含首次）= 首次 + 2 次重试。实测 1 次重试即 5/5 吸收，留一次余量。
DEFAULT_MAX_ATTEMPTS = 3
# 退避基数：第 n 次重试前睡 base_delay * n 秒。取小值是因为这发生在请求线程内，
# 而 E2E 对这条 POST 只给 15s 预算（frontend/e2e/trade-in-transit.spec.ts）。
DEFAULT_BASE_DELAY = 0.05


def is_retryable_deadlock(exc: BaseException) -> bool:
    """异常链里是否存在 MySQL 1213。

    要沿两处包装往里走：SQLAlchemy 把 DBAPI 异常挂在 `.orig` 上
    （`sqlalchemy.exc.OperationalError.orig` → `pymysql.err.OperationalError`），
    业务层还可能 `raise ... from ...` 再包一层（`__cause__` / `__context__`）。

    **刻意不做消息文本匹配**：只看 errno，否则任何消息里巧合含 "Deadlock" 字样的
    异常都会被误判成可重试，把真实故障洗成静默重放。
    """
    seen: set[int] = set()
    node: BaseException | None = exc
    # seen 去重是必要的：`raise x from x` 这类自指会让 __cause__ 形成环，
    # 不去重就是把一个死锁换成一个死循环。
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        args = getattr(node, "args", ()) or ()
        if args and args[0] == MYSQL_DEADLOCK:
            return True
        node = getattr(node, "orig", None) or node.__cause__ or node.__context__
    return False


def retry_on_deadlock(
    db,
    unit_of_work: Callable[[], T],
    *,
    operation: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> T:
    """跑一个自带事务边界的工作单元，遇 1213 整体回滚后有界重放。

    Args:
        db: 会话。重放前必须 `db.rollback()`——1213 已在服务端把整个事务回滚了，
            会话不复位的话下一次 flush 会抛 `PendingRollbackError`，把真因盖掉
            （同类教训见 backend/AGENTS.md 快照条目的 #419 段）。
        unit_of_work: 无参可调用，**必须自己 commit**。死锁可能在 service 的
            `flush()` 处抛出、也可能在 `commit()` 处抛出，重试范围必须同时覆盖
            两者，否则「commit 时才暴露的 1213」漏网。
        operation: 人可读的动作名，进日志的 `operation` 字段，便于按端点聚合
            （与 `app/error_reporting.py::report_unexpected` 同口径）。
        max_attempts: 总尝试次数（含首次）。
        base_delay: 退避基数秒；第 n 次重试前睡 `base_delay * n`。

    Returns:
        `unit_of_work()` 的返回值。

    Raises:
        原异常: 非 1213，或重试预算耗尽。耗尽时**刻意不** rollback——各端点的
            rollback 时机与错误码映射不同，留给调用点既有的 `except` 分支处理，
            保持响应契约不变。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return unit_of_work()
        except Exception as exc:
            if not is_retryable_deadlock(exc) or attempt >= max_attempts:
                raise
            db.rollback()
            delay = base_delay * attempt
            # WARNING 而非 ERROR：这次失败已被吸收、请求最终会成功，按日志规范的
            # 口径属可恢复事件；ERROR 留给真正冒到响应的失败（report_unexpected）。
            logger.warning(
                "MySQL 死锁(1213)：事务已整体回滚，重放本次工作单元",
                extra={
                    "operation": operation,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "retry_delay_seconds": delay,
                },
            )
            if delay > 0:
                time.sleep(delay)
