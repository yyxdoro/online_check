# Online Check

线上 Studio 文生模型检查脚本，当前实现已切换为 Python。

## 安装依赖

```bash
cd /Users/doro/Desktop/dingyue/online_check_py
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

`online_check_py` 自身已改为 Python；新注册账号仍复用外部脚本：

```text
/Users/doro/Desktop/dingyue/register/index.js
```

因此机器上仍需要可用的 `node` 命令来执行账号注册。

## 检查内容

`python3 -m src.index daily` 会串行执行两条链路，并合并结果发送一条飞书消息：

1. 固定账号密码登录生产 Studio 后发起文生模型任务。
2. 线上注册一个新的 `@otpebox.com` 测试账号，复用注册 session 后发起文生模型任务。

每条链路都会保存独立截图和 JSON，合并结果写入：

```text
/Users/doro/Desktop/dingyue/online_check_py/artifacts/latest-merged-result.json
```

## `.env`

配置文件放在：

```text
/Users/doro/Desktop/dingyue/online_check_py/.env
```

常用配置：

```bash
ACCOUNT_EMAIL=doroyang1@outlook.com
ACCOUNT_PASSWORD=your-password
FEISHU_APP_ID=your-feishu-app-id
FEISHU_APP_SECRET=your-feishu-app-secret
FEISHU_CHAT_ID=your-existing-bot-group-chat-id
ONLINE_CHECK_PROMPT=a cute low poly robot mascot, white background
HEADLESS=true
```

`FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和 `FEISHU_CHAT_ID` 存在时会通过飞书开放平台 HTTP API 发送文本和截图图片；否则通知会跳过并在结果 JSON 中记录 skipped。

## 统一入口

```bash
python3 -m src.index api
python3 -m src.index daily
python3 -m src.index studio --use-existing-account
python3 -m src.index password-login --email "doroyang1@outlook.com" --password "your-password"
python3 -m src.index subscription
```

> Python 版暂未实现飞书 WebSocket 长连接；检查和通知不依赖该功能。

## 对外 HTTP API 文档

### 服务启动

```bash
cd /Users/doro/Desktop/dingyue/online_check_py
python3 -m src.index api
```

默认监听 `0.0.0.0:8787`，可通过环境变量覆盖：

```bash
ONLINE_CHECK_API_HOST=0.0.0.0
ONLINE_CHECK_API_PORT=8787
# 或 PORT=8787
```

### 鉴权

如果配置了 `ONLINE_CHECK_API_TOKEN`，除健康检查外都需要带 Bearer Token：

```text
Authorization: Bearer ${ONLINE_CHECK_API_TOKEN}
```

未配置 `ONLINE_CHECK_API_TOKEN` 时，接口不校验鉴权。

### 通用响应

所有接口返回 `application/json; charset=utf-8`。

错误响应示例：

```json
{
  "ok": false,
  "error": "Unauthorized"
}
```

常见状态码：

- `200`：请求成功。
- `202`：检查任务已创建并在后台执行。
- `400`：请求参数不支持。
- `401`：鉴权失败。
- `404`：资源不存在。
- `409`：已有检查任务正在运行。

### GET `/online-check/health`

健康检查，不需要鉴权。

响应示例：

```json
{
  "ok": true,
  "activeJobId": null
}
```

字段说明：

- `ok`：服务是否可用。
- `activeJobId`：当前运行中的任务 ID；没有任务时为 `null`。

### POST `/online-check/run`

创建一个后台检查任务。若当前已有任务运行，会返回 `409`。

请求头：

```text
Content-Type: application/json
Authorization: Bearer ${ONLINE_CHECK_API_TOKEN}
```

请求体：

```json
{
  "mode": "daily",
  "notifyFeishu": false
}
```

字段说明：

- `mode`：检查模式，默认 `daily`。
  - `daily`：固定账号 + 新注册账号每日检查。
  - `full`：老用户登录 + 新注册账号 + 订阅购买全量检查。
  - `subscription`：订阅购买检查。
  - `password-login`：固定账号密码登录检查。
  - `registered-account`：新注册账号检查。
  - `existing-account`：复用已有注册账号检查。
- `notifyFeishu`：是否发送飞书通知。传 `false` 时不发送飞书消息；仅部分单链路模式读取该字段。
- `email`：仅 `password-login` 支持；不传则读取 `ACCOUNT_EMAIL`。
- `password`：仅 `password-login` 支持；不传则读取 `ACCOUNT_PASSWORD`。

响应示例：

```json
{
  "id": "5b6a7f6e-2c1e-4f11-8b43-8f2f1f4d2a11",
  "ok": false,
  "status": "running",
  "mode": "daily",
  "startedAt": "2026-06-08T12:00:00.000000Z"
}
```

调用示例：

```bash
curl -X POST http://localhost:8787/online-check/run \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${ONLINE_CHECK_API_TOKEN}" \
  -d '{"mode":"daily"}'
```

不发送飞书消息示例：

```bash
curl -X POST http://localhost:8787/online-check/run \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${ONLINE_CHECK_API_TOKEN}" \
  -d '{"mode":"password-login","notifyFeishu":false}'
```

### GET `/online-check/jobs/{jobId}`

查询指定任务状态。

路径参数：

- `jobId`：`POST /online-check/run` 返回的任务 ID。

响应示例：

```json
{
  "id": "5b6a7f6e-2c1e-4f11-8b43-8f2f1f4d2a11",
  "ok": true,
  "status": "succeeded",
  "mode": "daily",
  "startedAt": "2026-06-08T12:00:00.000000Z",
  "finishedAt": "2026-06-08T12:03:00.000000Z",
  "result": {
    "ok": true,
    "results": []
  }
}
```

`status` 可能值：

- `running`：执行中。
- `succeeded`：执行完成且结果成功。
- `failed`：执行失败或检查结果失败。

### GET `/online-check/jobs/latest`

查询最近一次 API 任务。服务会从 `artifacts/latest-api-job.json` 读取最近一次任务结果。

调用示例：

```bash
curl http://localhost:8787/online-check/jobs/latest \
  -H "Authorization: Bearer ${ONLINE_CHECK_API_TOKEN}"
```

### GET `/online-check/latest?type=merged`

读取最近一次检查结果文件。

查询参数：

- `type`：结果类型，默认 `merged`。
  - `merged`：`artifacts/latest-merged-result.json`
  - `latest`：`artifacts/latest-result.json`
  - `password-login`：`artifacts/password-login-latest-result.json`
  - `registered-account`：`artifacts/registered-account-latest-result.json`
  - `subscription`：`artifacts/latest-subscription-result.json`
  - `full`：`artifacts/latest-full-result.json`

调用示例：

```bash
curl 'http://localhost:8787/online-check/latest?type=merged' \
  -H "Authorization: Bearer ${ONLINE_CHECK_API_TOKEN}"
```


## 本地定时

本地使用 macOS launchd 部署时，建议直接调用 Python 入口：

```bash
/Users/doro/Desktop/dingyue/online_check_py/.venv/bin/python -m src.index daily
```

日志仍建议写入：

```text
/Users/doro/Desktop/dingyue/online_check_py/artifacts/launchd.out.log
/Users/doro/Desktop/dingyue/online_check_py/artifacts/launchd.err.log
```
