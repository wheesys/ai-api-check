"""API Key 对称加密（安全规则 3.5 + 设计 §10.5）。

红线：主密钥（master key）绝不入库、不入日志、不回显；api_key 仅以密文落库，
仅在服务端→上游请求时解密使用。

主密钥解析优先级：显式入参 > 环境变量 settings.api_key_master_key >
本地受限权限文件（缺省时首次生成，不随库提交）。
"""
import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

# 本地主密钥文件：仅属主可读写，须加入 .gitignore，绝不提交
MASTER_KEY_FILE = Path(".master.key")


class KeyManager:
    """基于 Fernet 的对称加解密器。"""

    def __init__(self, master_key: str | None = None) -> None:
        resolved_key = (
            master_key
            or settings.api_key_master_key
            or self._load_or_create_key_file()
        )
        self._fernet = Fernet(resolved_key.encode())

    @staticmethod
    def _load_or_create_key_file() -> str:
        """读取本地主密钥文件；不存在则生成并以 0600 权限落盘。"""
        if MASTER_KEY_FILE.exists():
            return MASTER_KEY_FILE.read_text(encoding="utf-8").strip()
        generated_key = Fernet.generate_key().decode()
        MASTER_KEY_FILE.write_text(generated_key, encoding="utf-8")
        try:
            MASTER_KEY_FILE.chmod(0o600)  # 仅属主可读写
        except OSError:
            # 部分文件系统（如某些容器卷）不支持 chmod，忽略不阻断
            pass
        # 仅提示生成事件，绝不打印密钥本身
        logger.warning(
            "已生成新的主密钥并写入 %s（请妥善备份；丢失将无法解密既有 api_key）",
            MASTER_KEY_FILE,
        )
        return generated_key

    def encrypt(self, plaintext: str) -> str:
        """加密明文，返回密文字符串。"""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """解密密文；密文损坏或主密钥不匹配时抛 ValueError。"""
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as error:
            raise ValueError("API Key 解密失败：密文损坏或主密钥不匹配") from error


_default_manager: KeyManager | None = None


def get_key_manager() -> KeyManager:
    """惰性单例：避免在模块导入时触发主密钥文件生成（利于测试隔离）。"""
    global _default_manager
    if _default_manager is None:
        _default_manager = KeyManager()
    return _default_manager
