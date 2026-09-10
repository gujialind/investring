from sqlalchemy import Column, String, Numeric, DateTime, Integer, ForeignKey, func
from app.constants.db_charset import DB_CHARSET, DB_COLLATE
from app.models.base import Base


class NavSyncDetail(Base):
    __tablename__ = "nav_sync_detail"

    # 显式 utf8mb4（#427 引入，#433 起全库同构）：error_message 接收外部数据源原文，
    # 4 字节字符在 utf8mb3 列上撞 errno 1366；本表写入直接 commit，故形态是外抛 500、
    # 中断净值同步——与四张日志表同型（静默丢失那档）。理由见 app/constants/db_charset.py。
    __table_args__ = {"mysql_charset": DB_CHARSET, "mysql_collate": DB_COLLATE}

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_log_id = Column(Integer, ForeignKey("task_execution_log.id"))
    # 显式命名（等价于 `op.f`）：迁移 0001 用 `op.create_foreign_key('fk_nav_sync_detail_job_id', …)`
    # 建过这条约束，而 `create_all` 对未命名 FK 用 MySQL 的 `<表>_ibfk_<n>` 约定——两者在真实库里
    # 会并存成同一列对上的两条 FK（生产库现状，已另提 issue）。去掉名字会让每次
    # `drop_all` + `create_all`（pytest 会话、CI 全新库）产生一个新序号的 `_ibfk_N`，
    # 与 0001 建的那条再叠一层。命名后两种路径落到同一个约束名。
    job_id = Column(
        Integer, ForeignKey("sync_job.id", name="fk_nav_sync_detail_job_id"),
        nullable=True, index=True,
    )
    product_code = Column(String(20), nullable=False)
    market = Column(String(20), nullable=False)
    nav_date = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    nav_value = Column(Numeric(10, 4))
    synced_count = Column(Integer, default=0)
    source = Column(String(20))
    error_message = Column(String(500))
    created_at = Column(DateTime, server_default=func.now())
