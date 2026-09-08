from pydantic_settings import BaseSettings
from functools import lru_cache

# 哨兵默认值：仅为启动校验给出明确报错，任何环境都禁止实际使用（issue #255）
INSECURE_DEFAULT_SECRET_KEY = "your-secret-key-change-in-production"


class Settings(BaseSettings):
    # Database
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "investring"
    database_url: str = ""
    
    # Security
    secret_key: str = INSECURE_DEFAULT_SECRET_KEY
    token_expire_days: int = 7
    
    # Tushare API
    tushare_token: str = ""
    tushare_rate_interval: float = 0.5
    tushare_max_retries: int = 3
    tushare_rate_limit_backoff: str = "10,30,60"

    # AkShare API
    akshare_enabled: bool = False
    akshare_rate_interval: float = 1.0
    akshare_max_retries: int = 3

    # 后台线程池
    sync_worker_count: int = 2

    # APScheduler
    scheduler_enabled: bool = True
    scheduler_cron_daily: str = "0 7 * * *"
    scheduler_cron_snapshot: str = "30 7 * * *"
    scheduler_jobstore_table: str = "apscheduler_jobs"

    # Application
    app_name: str = "InvestRing"
    debug: bool = True
    # 日志级别；空 = 由 debug 推导（True→DEBUG / False→INFO），显式设置则覆盖（issue #404）
    log_level: str = ""
    
    class Config:
        env_file = ".env"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.secret_key == INSECURE_DEFAULT_SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY 仍为默认占位值，拒绝启动（issue #255）。"
                "请生成随机强密钥（≥32 字节，如 `openssl rand -hex 32`）"
                "并写入 backend/.env 的 SECRET_KEY 或环境变量"
            )
        if not self.database_url:
            self.database_url = f"mysql+pymysql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
