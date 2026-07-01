# Imagefree2API

将 [imagefree.org](https://imagefree.org) 的免费 AI 图片生成能力封装为 **OpenAI 兼容**的 REST API。

无需注册、无需 API Key 给 imagefree，只需自备 **Capsolver API Key**（约 ¥0.0087/次）。

## ✨ 特性

- ✅ **OpenAI 兼容** — 直接替换 `https://api.openai.com/v1/images/generations`
- ✅ **完全免费** — imagefree 端 0 成本，仅需 capsolver 打码费（约 1 分钱/次）
- ✅ **无需注册** — 不需要 imagefree 账号
- ✅ **高并发** — 支持多请求排队
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
curl -X POST http://localhost:7860/v1/images/generations \
  -H "Authorization: Bearer sk-imagefree2api-xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "imagefree",
    "prompt": "a futuristic cyberpunk cat with neon eyes",
    "n": 1,
    "size": "1024x1024"
  }'
```

**响应示例：**

```json
{
  "created": 1718123456,
  "data": [
    {
      "url": "https://pub-89a5b0102174408d8d7f88dcf07eec20.r2.dev/images/2026/07/01/uuid.png"
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
curl http://localhost:7860/health
```

### 列出模型

```bash
curl http://localhost:7860/v1/models \
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
