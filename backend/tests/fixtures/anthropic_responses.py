"""Anthropic 协议录制响应夹具（脱敏，无真实 Key）。"""

# 非流式 /v1/messages 响应
MESSAGE_RESPONSE = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Hello!"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 9, "output_tokens": 2},
}

# /v1/models 列表响应
MODELS_LIST = {
    "data": [
        {"id": "claude-3-5-sonnet-20241022", "type": "model", "display_name": "Claude 3.5 Sonnet"},
        {"id": "claude-3-5-haiku-20241022", "type": "model", "display_name": "Claude 3.5 Haiku"},
    ],
}

# 流式 SSE 行（event 行 + data 行交替；含 message_start/content_block_delta/message_delta）
STREAM_LINES = [
    b"event: message_start",
    b'data: {"type":"message_start","message":{"usage":{"input_tokens":9,"output_tokens":0}}}',
    b"event: content_block_delta",
    b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}',
    b"event: content_block_delta",
    b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}',
    b"event: message_delta",
    b'data: {"type":"message_delta","usage":{"output_tokens":2}}',
    b"event: message_stop",
    b'data: {"type":"message_stop"}',
]
