import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app.database import get_db
from app.schemas.data_source import DataSourceResponse, DataSourceUpdate
from app.dependencies import get_current_user, get_current_admin
from app.models.product import Product
from app.services.exceptions import BusinessError
from app.services.null_guard import reject_explicit_nulls

router = APIRouter()


def _mask_api_key(api_key: str) -> str:
    """
    脱敏 API Key
    显示前 4 位和后 4 位，中间用 * 替换
    """
    if not api_key or len(api_key) <= 8:
        return "***"
    return api_key[:4] + "*" * (len(api_key) - 8) + api_key[-4:]


def _get_data_sources(db: Session) -> List[dict]:
    """
    获取数据源配置列表
    从 .env 文件和数据库读取数据源信息
    """
    # Tushare 数据源
    tushare_token = os.environ.get("TUSHARE_TOKEN", "")
    
    # 获取 Tushare 最后同步时间（从产品中获取最新的 last_sync_at）
    tushare_last_sync = db.query(Product).filter(
        Product.data_source == "tushare",
        Product.last_sync_at.isnot(None)
    ).order_by(Product.last_sync_at.desc()).first()

    # AkShare 数据源
    akshare_enabled = os.environ.get("AKSHARE_ENABLED", "true").lower() == "true"

    sources = [
        {
            "name": "tushare",
            "api_key": _mask_api_key(tushare_token) if tushare_token else None,
            "is_enabled": bool(tushare_token),
            "last_sync_at": tushare_last_sync.last_sync_at if tushare_last_sync else None,
            "created_at": None,
            "updated_at": None,
        },
        {
            "name": "akshare",
            "api_key": None,  # AkShare 不需要 API Key
            "is_enabled": akshare_enabled,
            "last_sync_at": None,
            "created_at": None,
            "updated_at": None,
        },
    ]

    return sources


@router.get("")
def get_data_sources(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    获取数据源配置列表
    
    返回 Tushare 和 AkShare 的配置信息
    API Key 会进行脱敏处理
    """
    return _get_data_sources(db)


@router.put("/{name}")
def update_data_source(
    name: str,
    data_source: DataSourceUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    """
    更新数据源配置
    
    支持更新：
    - tushare: 更新 TUSHARE_TOKEN
    - akshare: 更新 AKSHARE_ENABLED

    显式 null 一律 422 拒绝（#579，与 #573 同口径）；未提供或空值零写入，
    message 如实报「未变更」，不再谎报「已更新」。写入值经 `_reject_unsafe_env_value`
    收口（#601 评审 follow-up：防 .env 任意键注入）。
    """
    if name not in ("tushare", "akshare"):
        # 404 专用码先于通用收口（#576 评审 🟡3 同款教训：不让 INVALID_PARAM 抢占）
        raise HTTPException(status_code=404, detail=f"未知的数据源: {name}")

    updates = data_source.dict(exclude_unset=True)
    reject_explicit_nulls(updates)

    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")

    if name == "tushare":
        # 更新 Tushare Token；未提供或空串 = 零写入（清空 token 不是已支持能力）
        api_key = updates.get("api_key")
        if api_key:
            _reject_unsafe_env_value(api_key)
            _update_env_file(env_file, "TUSHARE_TOKEN", api_key)
            os.environ["TUSHARE_TOKEN"] = api_key
            message = "Tushare 配置已更新"
        else:
            message = "api_key 未提供或为空，Tushare 配置未变更"
        return {
            "message": message,
            "name": "tushare",
            "is_enabled": bool(os.environ.get("TUSHARE_TOKEN")),
        }

    # akshare：更新启用状态；未提供 = 零写入
    if "is_enabled" in updates:
        value = str(updates["is_enabled"]).lower()
        _reject_unsafe_env_value(value)
        _update_env_file(env_file, "AKSHARE_ENABLED", value)
        os.environ["AKSHARE_ENABLED"] = value
        message = "AkShare 配置已更新"
    else:
        message = "is_enabled 未提供，AkShare 配置未变更"
    return {
        "message": message,
        "name": "akshare",
        "is_enabled": os.environ.get("AKSHARE_ENABLED", "true").lower() == "true",
    }


def _reject_unsafe_env_value(value: str) -> None:
    """拒绝无法安全整行写入 .env 的取值（#601 评审 follow-up）。

    写入形态是 `f"{key}={value}\\n"`，而 .env 按行解析：value 里含 `\\n`/`\\r` 时，
    其后内容会另起一行成为新的配置项，下次进程启动经 app/config.py 的 env_file
    解析后即成为真实环境变量——也就是任意键注入（例如 `"tok\\nAKSHARE_ENABLED=false"`）。
    不可见的控制字符同样会污染文件且难以排查，故一并拒绝（含 TAB、NUL、DEL）。

    由调用方在写前调用本函数（而非塞进 `_update_env_file` 内部）：集成测试把
    `_update_env_file` 换成调用记录来断言「零写入」，放在内部就测不到这道闸门。
    """
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise BusinessError(
            "INVALID_PARAM",
            "配置值不可包含换行或控制字符",
        )


def _update_env_file(env_file: str, key: str, value: str) -> None:
    """
    更新 .env 文件中的配置项
    
    如果配置项已存在则更新，不存在则追加

    前置条件：value 已过 `_reject_unsafe_env_value`（整行写入，值必须为单行安全）。
    """
    lines = []
    key_found = False
    
    # 读取现有文件
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    
    # 查找并更新配置项
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped == key:
            lines[i] = f"{key}={value}\n"
            key_found = True
            break
    
    # 如果配置项不存在，追加到文件末尾
    if not key_found:
        lines.append(f"{key}={value}\n")
    
    # 写回文件
    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
