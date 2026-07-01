# Imagefree2API

将 [imagefree.org](https://imagefree.org) 的免费 AI 图片生成能力封装为 **OpenAI 兼容**的 REST API。

无需注册、无需 API Key 给 imagefree，只需自备 **Capsolver API Key**（约 ¥0.0087/次）。

## ✨ 特性

- ✅ **OpenAI 兼容** — 直接替换 `https://api.openai.com/v1/images/generations`
- ✅ **完全免费** — imagefree 端 0 成本，仅需 capsolver 打码费（约 1 分钱/次）
- ✅ **无需注册** — 不需要 imagefree 账号
- ✅ **Cookie 号池** — 多个独立 session 轮询使用，失败自动冷却
- ✅ **高并发** — 支持多请求排队，可按 session 数量扩容
- ✅ **Docker 一键部署**
- ✅ **API Key 鉴权** — 内置 Bearer Token 认证

## 🚀 快速开始

### 1. 准备工作

```bash
# 克隆项目
git clone https://github.com/YOUR_USERNAME/imagefree2api.git
cd imagefree2api

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 CAPSOLVER_API_KEY
```

### 2. Docker 部署

```bash
docker compose up -d
```

### 3. 查看 API Key

部署后会在启动日志中打印 API Key：

```bash
docker logs imagefree2api 2>&1 | grep "API Key"
```

或从 `.env` 文件中查看。

## 📡 API 文档

### 生成图片

```bash
curl -X POST http://localhost:7861/v1/images/generations \
  -H "Authorization: Bearer sk-imagefree2api-xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "a futuristic cyberpunk cat with neon eyes",
    "n": 1,
    "size": "1024x1024",
    "response_format": "b64_json"
  }'
```

**响应示例：**

```json
{
  "created": 1718123456,
  "data": [
    {
      "b64_json": "iVBORw0KGgoAAAANSUhEUgAA...",
      "revised_prompt": "a futuristic cyberpunk cat with neon eyes"
    }
  ]
}
```
### 支持的尺寸

| 参数值 | 比例 |
|--------|------|
| `1024x1024` | 1:1 |
| `768x1024`  | 3:4  |
| `1024x768`  | 4:3  |
| `512x1024`  | 9:16 |
| `1024x512`  | 16:9 |

### 健康检查

```bash
curl http://localhost:7861/health
```

### 列出模型

```bash
curl http://localhost:7861/v1/models \
  -H "Authorization: Bearer sk-imagefree2api-xxxxxxxxxxxxxxxx"
```

## 🔧 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `CAPSOLVER_API_KEY` | — | **必填** Capsolver API Key |
| `API_KEY` | 自动生成 | API 访问密钥 |
| `PORT` | `7860` | 服务端口 |
| `MAX_CONCURRENCY` | `1` | 最大并发数 |
| `REQUEST_INTERVAL_SECONDS` | `30` | 多图生成间隔（秒） |

也可以在 `config.yaml` 中配置：

```yaml
generation:
  max_concurrency: 6
  request_interval: 30
  output_dir: "/data/images"
  session_pool:
    enabled: true
    session_count: 6
    max_concurrent_per_session: 1
    cooldown_seconds: 60
    wait_timeout_seconds: 180
```

## 🍪 Cookie 号池策略

`imagefree.org` 会使用浏览器会话 Cookie、访客 ID 和 Turnstile 验证状态。单一 Cookie 在并发请求下容易互相影响，所以项目内置了 session 号池：

- 启动时创建 `session_count` 个独立 session，每个 session 有独立的 `visitor_id`、`session_id` 和 Cookie。
- 每次生成图片前从号池借出一个可用 session，默认轮询分配，避免所有请求挤到同一个 Cookie。
- `max_concurrent_per_session` 控制单个 session 同时处理的请求数，建议保持 `1`，让每个 Cookie 串行工作。
- 总并发能力约等于 `session_count * max_concurrent_per_session`，同时还会受 `generation.max_concurrency` 全局限制。
- 请求完成后会把上游返回的新 Cookie 写回对应 session，下次继续复用。
- Cookie 和访客信息会持久化到 `output_dir` 的上一级目录下的 `session_pool.json`，服务重启后会继续使用原来的号池状态。
- 某个 session 请求失败时会进入冷却，冷却时间为 `cooldown_seconds * 连续失败次数`，最多放大到 `cooldown_seconds * 5`。
- 如果所有 session 都在使用中或冷却中，请求会等待；超过 `wait_timeout_seconds` 仍没有可用 session，就返回超时错误。

推荐生产配置是多个 session、每个 session 一个并发槽，例如 `session_count: 6`、`max_concurrent_per_session: 1`。这样比单 Cookie 高并发更稳定，也更容易隔离失败。

## 💰 费用估算

| 每日调用量 | Capsolver 费用 | 约合人民币 |
|-----------|---------------|-----------|
| 100 次 | $0.12 | ¥0.87 |
| 1,000 次 | $1.20 | ¥8.70 |
| 10,000 次 | $12.00 | ¥87.00 |

## ⚠️ 免责声明

本项目仅供学习和研究用途。使用请遵守 imagefree.org 的服务条款。

## 📄 License

MIT
