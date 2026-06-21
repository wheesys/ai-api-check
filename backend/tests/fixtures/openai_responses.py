"""OpenAI 协议录制响应夹具（脱敏，无真实 Key）。"""

# 非流式 chat/completions 响应
CHAT_COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
}

# /v1/models 列表响应
MODELS_LIST = {
    "object": "list",
    "data": [
        {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
        {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
    ],
}

# 流式 SSE 行（含末帧 usage 与 [DONE] 终止符）
STREAM_LINES = [
    b'data: {"choices":[{"delta":{"content":"Hel"}}]}',
    b'data: {"choices":[{"delta":{"content":"lo"}}]}',
    b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":9,"completion_tokens":2,"total_tokens":11}}',
    b"data: [DONE]",
]
