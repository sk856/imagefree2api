# API 使用文档

这是 OpenAI 兼容的聊天接口，将消息转换为图像生成提示。

#### 请求示例（非流式）

```bash
curl -X POST http://your-server:7861/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "imagefree",
    "messages": [
      {"role": "user", "content": "A beautiful sunset over mountains"}
    ],
    "size": "1024x1024"
  }'
```

#### 响应示例

```json
{
  "id": "chatcmpl-cb8c32c0fefe4a0da786cd10",
  "object": "chat.completion",
  "created": 1782900839,
  "model": "imagefree",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "![Generated Image](https://example.com/image.png)\n\nI've generated an image based on your prompt: \"A beautiful sunset over mountains\""
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 3,
    "completion_tokens": 13,
    "total_tokens": 16
  }
}
```

#### 请求示例（流式）

```bash
curl -X POST http://your-server:7861/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "imagefree",
    "messages": [
      {"role": "user", "content": "A cyberpunk cityscape"}
    ],
    "size": "1024x1024",
    "stream": true
  }'
```

#### 流式响应示例

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1782900869,"model":"imagefree","choices":[{"index":0,"delta":{"role":"assistant","content":null},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1782900869,"model":"imagefree","choices":[{"index":0,"delta":{"role":null,"content":"![Generated Image](https://example.com/image.png)"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1782900869,"model":"imagefree","choices":[{"index":0,"delta":{"role":null,"content":null},"finish_reason":"stop"}]}

data: [DONE]
```

### 2. `/v1/responses` - 响应接口

这是 `/v1/chat/completions` 的别名接口，提供相同的功能，用于兼容特定客户端。

#### 请求示例

```bash
curl -X POST http://your-server:7861/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "imagefree",
    "messages": [
      {"role": "user", "content": "A cute cat playing with yarn"}
    ],
    "size": "1024x1024"
  }'
```

响应格式与 `/v1/chat/completions` 完全相同。

## 参数说明

### 请求参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 否 | imagefree | 模型名称 |
| messages | array | 是 | - | 消息列表，最后一条用户消息将作为图像生成提示 |
| size | string | 否 | 1024x1024 | 图像尺寸，支持：1024x1024, 768x1024, 1024x768, 512x1024, 1024x512 |
| stream | boolean | 否 | false | 是否使用流式响应 |
| temperature | float | 否 | 1.0 | 温度参数（保留兼容性，不影响图像生成） |
| n | int | 否 | 1 | 生成图像数量（1-4） |

### 消息格式

```json
{
  "role": "user|assistant|system",
  "content": "消息内容"
}
```

- `role`: 消息角色，只有 `user` 角色的消息会被用作图像生成提示
- `content`: 消息内容，将作为图像生成的提示词

## 支持的图像尺寸

- `1024x1024` - 正方形 (1:1)
- `768x1024` - 竖向 (3:4)
- `1024x768` - 横向 (4:3)
- `512x1024` - 竖向长图 (9:16)
- `1024x512` - 横向长图 (16:9)

## 错误响应

### 401 未授权

```json
{
  "detail": "Invalid API key"
}
```

### 400 请求错误

```json
{
  "detail": "No user message found in the conversation"
}
```

### 502 服务错误

```json
{
  "detail": "Image generation failed: ..."
}
```

## 完整接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/v1/models` | GET | 列出可用模型 |
| `/v1/images/generations` | POST | OpenAI 图像生成接口 |
| `/v1/chat/completions` | POST | OpenAI 聊天补全接口（图像生成） |
| `/v1/responses` | POST | 响应接口（与 chat/completions 相同） |

## Python 客户端示例

```python
import requests

API_KEY = "sk-imagefree2api-xxx"
BASE_URL = "http://your-server:7861"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# 非流式请求
response = requests.post(
    f"{BASE_URL}/v1/chat/completions",
    headers=headers,
    json={
        "model": "imagefree",
        "messages": [
            {"role": "user", "content": "A beautiful landscape"}
        ],
        "size": "1024x1024"
    }
)

result = response.json()
print(result["choices"][0]["message"]["content"])

# 流式请求
response = requests.post(
    f"{BASE_URL}/v1/chat/completions",
    headers=headers,
    json={
        "model": "imagefree",
        "messages": [
            {"role": "user", "content": "A space station"}
        ],
        "size": "1024x1024",
        "stream": True
    },
    stream=True
)

for line in response.iter_lines():
    if line:
        print(line.decode('utf-8'))
```

## OpenAI SDK 兼容性

可以使用 OpenAI Python SDK 访问这些接口：

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-imagefree2api-xxx",
    base_url="http://your-server:7861/v1"
)

response = client.chat.completions.create(
    model="imagefree",
    messages=[
        {"role": "user", "content": "A futuristic robot"}
    ],
    extra_body={"size": "1024x1024"}
)

print(response.choices[0].message.content)
```

## 注意事项

1. 此服务仅支持图像生成，不支持文本对话
2. 用户消息的 `content` 将直接作为图像生成的提示词
3. 生成一张图像通常需要 30-60 秒
4. 图像 URL 返回后，链接通常有效期较长
5. API 密钥必须在请求头的 `Authorization: Bearer YOUR_KEY` 中提供
