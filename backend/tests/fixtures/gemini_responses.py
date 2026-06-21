"""Gemini 协议录制响应夹具（脱敏，无真实 Key）。

覆盖原生 Developer / Vertex 风格的 generateContent、模型列表、流式 SSE、
:countTokens，以及 OpenAI 兼容层模型列表。功能性指纹字段（thoughtsTokenCount /
safetyRatings / 代码执行 / modelVersion）按设计 §7.5 内置，供指纹探针判定。
"""

# 原生 generateContent 响应（含思考用量、安全评级、modelVersion 指纹字段）
GENERATE_CONTENT = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"text": "Hello from Gemini!"}],
            },
            "finishReason": "STOP",
            "safetyRatings": [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "probability": "NEGLIGIBLE",
                }
            ],
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 5,
        "candidatesTokenCount": 4,
        "totalTokenCount": 9,
        "thoughtsTokenCount": 12,
    },
    "modelVersion": "gemini-2.5-pro",
}

# 代码执行功能响应（parts 含 executableCode + codeExecutionResult 特有结构）
GENERATE_CONTENT_CODE_EXEC = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {"executableCode": {"language": "PYTHON", "code": "print(2 + 2)"}},
                    {"codeExecutionResult": {"outcome": "OUTCOME_OK", "output": "4\n"}},
                    {"text": "结果是 4。"},
                ],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 8,
        "candidatesTokenCount": 10,
        "totalTokenCount": 18,
    },
}

# Developer 风格模型列表（name 形如 models/gemini-2.5-pro）
MODELS_LIST = {
    "models": [
        {
            "name": "models/gemini-2.5-pro",
            "displayName": "Gemini 2.5 Pro",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
            "inputTokenLimit": 1048576,
        },
        {
            "name": "models/gemini-2.5-flash",
            "displayName": "Gemini 2.5 Flash",
        },
    ]
}

# 原生流式 SSE 行（末帧携带 usageMetadata）
STREAM_LINES = [
    b'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hello"}]}}]}',
    b'data: {"candidates":[{"content":{"role":"model","parts":[{"text":" Gemini"}]}}]}',
    b'data: {"candidates":[{"finishReason":"STOP"}],'
    b'"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":4,"totalTokenCount":9}}',
]

# :countTokens 响应
COUNT_TOKENS = {"totalTokens": 9}

# OpenAI 兼容层 /v1/models 响应（含非 Gemini 模型，用于校验过滤）
COMPAT_MODELS_LIST = {
    "object": "list",
    "data": [
        {"id": "gemini-2.5-pro", "object": "model"},
        {"id": "gpt-4o-mini", "object": "model"},
    ],
}

# OpenAI 兼容层 chat 响应（Gemini 经兼容层转出，结构为 OpenAI 风格）
COMPAT_CHAT_COMPLETION = {
    "id": "chatcmpl-gemini",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hi via compat layer"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 6, "completion_tokens": 5, "total_tokens": 11},
}
