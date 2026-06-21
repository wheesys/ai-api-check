"""本地 token 估算器（设计 §8.4 计费一致性的对照基线）。

用于离线估算文本 token 数，与上游申报 usage 比对偏差，校验计费真实性。
默认用 tiktoken `cl100k_base`（协议无关基线）；编码加载失败时退化为字符近似，
保证探针永不因 tokenizer 不可用而硬失败（设计 §11 边界容错）。

注意：这是"对照估算"而非精确账单；不同协议 tokenizer 切分有差异，故偏差判定
留有阈值带（pass/degraded/fail），并对申报缺失场景降低置信度。
"""
from collections.abc import Callable

from app.providers.base import ChatMessage

# 字符近似兜底：中英文混合下约 4 字符/token 的经验系数（仅在 tiktoken 不可用时启用）
_CHARS_PER_TOKEN = 4
# 每条消息的结构性开销（role 包裹等），与 OpenAI 计费约定近似对齐
_PER_MESSAGE_OVERHEAD = 4


class TokenEstimator:
    """本地 token 估算器：优先 tiktoken，失败退化为字符近似。"""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._encoding_name = encoding_name
        self._encode: Callable[[str], list[int]] | None = self._load_encoder()

    def _load_encoder(self) -> Callable[[str], list[int]] | None:
        """惰性加载 tiktoken 编码器；任何加载异常都降级为 None（走字符近似）。"""
        try:
            import tiktoken

            encoding = tiktoken.get_encoding(self._encoding_name)
            return encoding.encode
        except Exception:
            # 离线/缺编码文件/未安装：不抛错，交由字符近似兜底
            return None

    @property
    def is_exact(self) -> bool:
        """当前是否使用精确分词（影响计费一致性置信度）。"""
        return self._encode is not None

    def estimate(self, text: str) -> int:
        """估算单段文本的 token 数。"""
        if not text:
            return 0
        if self._encode is not None:
            return len(self._encode(text))
        # 字符近似：向上取整，避免低估
        return -(-len(text) // _CHARS_PER_TOKEN)

    def estimate_messages(self, messages: list[ChatMessage]) -> int:
        """估算多轮消息的输入 token 数（含每条消息结构开销）。"""
        total = 0
        for message in messages:
            total += self.estimate(message.content) + _PER_MESSAGE_OVERHEAD
        return total
