from sqlalchemy import Column, JSON, String, Text, DateTime, Boolean, func, text
from app.models.base import Base


class Portfolio(Base):
    __tablename__ = "portfolio"

    code = Column(String(20), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    status = Column(String(20), default="draft")
    # 持仓明细二级分组维度覆盖（issue #144）：{"ASSET_STOCK": "style", ...}，
    # 仅存显式覆盖项；NULL = 未配置 = 前端内置默认；校验见 portfolio_service
    display_config = Column(JSON)
    # 自动快照开关（issue #156）：默认 False（opt-in），仅约束自动任务
    # （snapshot_generate），手动生成/重算端点不受影响
    auto_snapshot_enabled = Column(Boolean, default=False, server_default=text("0"), nullable=False)
    started_at = Column(DateTime)
    closed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
