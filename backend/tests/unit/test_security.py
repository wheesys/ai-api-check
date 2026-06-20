"""安全模块单元测试：加密往返 + 脱敏。

零网络；测试内构造的 Key 均为伪造样例，绝非真实凭据。
"""
import pytest
from cryptography.fernet import Fernet

from app.security.crypto import KeyManager
from app.security.sanitizer import ErrorSanitizer

# 测试专用主密钥，避免触碰本地密钥文件（隔离）
_TEST_MASTER_KEY = Fernet.generate_key().decode()
# 伪造样例凭据（非真实 Key，仅用于断言脱敏生效）
_FAKE_OPENAI_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0"
_FAKE_ANTHROPIC_KEY = "sk-ant-" + "Z9y8X7w6V5u4T3s2R1q0"
_FAKE_GOOGLE_KEY = "AIza" + "SyD1234567890abcdEFGH"


@pytest.fixture
def key_manager() -> KeyManager:
    """使用显式主密钥构造，不读写本地文件。"""
    return KeyManager(master_key=_TEST_MASTER_KEY)


def test_encrypt_then_decrypt_roundtrip(key_manager):
    """加密后再解密应还原明文。"""
    plaintext = _FAKE_OPENAI_KEY
    ciphertext = key_manager.encrypt(plaintext)
    assert ciphertext != plaintext  # 密文不等于明文
    assert plaintext not in ciphertext  # 密文中不含明文片段
    assert key_manager.decrypt(ciphertext) == plaintext


def test_decrypt_rejects_tampered_ciphertext(key_manager):
    """损坏密文应抛 ValueError 而非静默返回。"""
    with pytest.raises(ValueError):
        key_manager.decrypt("not-a-valid-token")


def test_different_managers_cannot_cross_decrypt(key_manager):
    """主密钥不匹配时无法解密，验证密钥隔离性。"""
    other = KeyManager(master_key=Fernet.generate_key().decode())
    ciphertext = key_manager.encrypt(_FAKE_ANTHROPIC_KEY)
    with pytest.raises(ValueError):
        other.decrypt(ciphertext)


@pytest.mark.parametrize(
    "fake_key",
    [_FAKE_OPENAI_KEY, _FAKE_ANTHROPIC_KEY, _FAKE_GOOGLE_KEY],
)
def test_sanitize_redacts_known_key_formats(fake_key):
    """常见 Key 格式均被脱敏，结果不含原始 Key。"""
    text = f"upstream error: invalid key {fake_key} please check"
    sanitized = ErrorSanitizer.sanitize(text)
    assert fake_key not in sanitized
    assert "[REDACTED]" in sanitized


def test_sanitize_redacts_bearer_token():
    """Bearer token 被脱敏，保留 Bearer 字样。"""
    sanitized = ErrorSanitizer.sanitize("Authorization: Bearer abcDEF123456token")
    assert "abcDEF123456token" not in sanitized
    assert "[REDACTED]" in sanitized


def test_sanitize_redacts_api_key_assignment():
    """形如 x-api-key=xxx 的赋值被脱敏，保留键名。"""
    sanitized = ErrorSanitizer.sanitize('{"x-api-key": "secretValue123456"}')
    assert "secretValue123456" not in sanitized
    assert "x-api-key" in sanitized


def test_sanitize_passthrough_for_empty():
    """空值/None 原样返回，不抛错。"""
    assert ErrorSanitizer.sanitize(None) is None
    assert ErrorSanitizer.sanitize("") == ""
    assert ErrorSanitizer.sanitize("正常文本无凭据") == "正常文本无凭据"


def test_sanitize_headers_drops_sensitive_values():
    """敏感请求头值被替换为 [REDACTED]，普通头保留。"""
    headers = {
        "Authorization": "Bearer secret",
        "X-Api-Key": "AIzaSecret",
        "Content-Type": "application/json",
    }
    masked = ErrorSanitizer.sanitize_headers(headers)
    assert masked["Authorization"] == "[REDACTED]"
    assert masked["X-Api-Key"] == "[REDACTED]"
    assert masked["Content-Type"] == "application/json"
