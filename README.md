# HTTP Proxy for Trae IDE

这是一个用于 Trae IDE 的 HTTP 代理服务器，用于在转发 AI 模型请求时自动注入 thinking 参数。

## 功能特性

- ✅ 支持 Anthropic 和 OpenAI 请求格式
- ✅ 自动注入 thinking 参数
- ✅ 支持多模型配置
- ✅ 流式响应传输
- ✅ 详细的日志记录

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv venv
.\venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置文件

编辑 `config.yaml` 文件，填入你的 API 密钥：

```yaml
models:
  - name: "claude-3-5-sonnet-20241022"
    provider: "anthropic"
    api_base: "https://api.anthropic.com"
    api_key: "YOUR_ANTHROPIC_API_KEY"  # 替换为你的密钥
```

### 3. 启动服务器

```bash
python proxy_server.py
```

服务器将在 `http://127.0.0.1:8080` 启动。

### 4. 在 Trae IDE 中配置

将 Trae IDE 的请求地址指向：`http://127.0.0.1:8080`

## 配置说明

### 代理服务器配置

```yaml
proxy:
  host: "127.0.0.1"  # 监听地址
  port: 8080          # 监听端口
```

### Thinking 参数配置

```yaml
thinking:
  enabled: true        # 是否启用 thinking
  budget_tokens: 16000 # thinking token 预算
```

### 模型配置

每个模型需要配置：
- `name`: 模型名称
- `provider`: 提供商（anthropic 或 openai）
- `api_base`: API 基础地址
- `api_key`: API 密钥

### 日志配置

```yaml
logging:
  level: "INFO"      # 日志级别：DEBUG, INFO, WARNING, ERROR
  file: "proxy.log"  # 日志文件路径
```

## 请求示例

代理会自动识别请求类型并注入 thinking 参数：

**原始请求：**
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 1024
}
```

**注入后的请求：**
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 1024,
  "thinking": {
    "type": "enabled",
    "budget_tokens": 16000
  }
}
```

## 支持的模型

### Anthropic
- claude-3-5-sonnet-20241022
- claude-3-5-haiku-20241022
- 其他 Claude 系列模型

### OpenAI
- gpt-4o
- gpt-4o-mini
- 其他 GPT 系列模型

## 故障排除

### 1. 模块未找到错误
确保已激活虚拟环境并安装了所有依赖：
```bash
pip install -r requirements.txt
```

### 2. 端口被占用
修改 `config.yaml` 中的 `port` 配置为其他端口。

### 3. API 密钥错误
检查 `config.yaml` 中的 `api_key` 是否正确。

## 开发说明

- 日志文件：`proxy.log`
- 配置文件：`config.yaml`
- 主程序：`proxy_server.py`
