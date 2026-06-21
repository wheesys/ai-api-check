"""应用配置：从环境变量 / .env 加载，遵循安全规则（主密钥不入库、不回显）。"""
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置单例。所有可调参数集中于此，避免散落硬编码。"""

    # ---- 服务 ----
    debug: bool = False
    host: str = "localhost"
    port: int = 8000

    # ---- 数据库 ----
    database_url: str = "sqlite:///./app.db"

    # ---- 安全：API Key 加密主密钥 ----
    # 优先取自环境变量；缺省时首次启动生成临时密钥（仅开发用，重启后已加密 Key 不可解）。
    api_key_master_key: str = ""

    # ---- CORS（前端开发地址）----
    cors_origins: list[str] = ["http://localhost:5173"]

    # ---- 并发控制 ----
    max_concurrent_tasks: int = 2  # 任务间全局并发上限（本地场景建议 2~3）
    default_max_concurrency_per_task: int = 2  # 单任务内同类别探针并发上限

    # ---- 超时（秒）----
    request_timeout_seconds: float = 30.0  # 单次上游请求超时
    task_timeout_seconds: float = 300.0  # 单任务总超时兜底（防探针卡死拖垮整任务）

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_comma_separated(cls, value):
        """支持 .env 中以逗号分隔的字符串（如 a,b,c），自动拆分为列表。"""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
