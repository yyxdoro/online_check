# online_check_py 线上部署说明

部署目标：在服务器上定时执行线上 Studio 巡检，覆盖老用户登录、新注册账号、订阅购买，写入飞书多维表格并发送报告。

## 1. 目录规划

推荐路径：

```bash
/home/ec2-user/yuha/online_check_py
/home/ec2-user/yuha/register
```

`online_check_py` 依赖相邻的 `register/index.js` 做线上新账号注册，所以两个目录都要部署。

## 2. 同步代码

从本机同步到服务器示例：

```bash
rsync -av --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pycache_tmp' \
  --exclude 'artifacts/*.png' \
  /Users/doro/Desktop/dingyue/online_check_py/ \
  ec2-user@<server>:/home/ec2-user/yuha/online_check_py/

rsync -av --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  /Users/doro/Desktop/dingyue/register/ \
  ec2-user@<server>:/home/ec2-user/yuha/register/
```

不要用 `--delete` 覆盖服务器私有 `.env`，除非确认本地 `.env` 就是线上配置。更稳的做法是单独在服务器维护 `.env`。

## 3. 安装系统依赖

Amazon Linux / EC2 上：

```bash
sudo yum install -y python3.12 nodejs npm
```

如果没有 `python3.12`，用系统可安装的 Python 3.12 包或 pyenv/uv 安装。项目使用 `Path | None` 等语法，不能用 Python 3.9 运行。

## 4. 安装 Python 依赖和浏览器

```bash
cd /home/ec2-user/yuha/online_check_py
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
```

`requirements.txt` 当前需要：

```text
playwright
requests
```

## 5. 配置 .env

服务器文件：

```bash
/home/ec2-user/yuha/online_check_py/.env
```

关键配置：

```bash
ACCOUNT_EMAIL=老用户账号
ACCOUNT_PASSWORD=老用户密码
HEADLESS=true

FEISHU_APP_ID=飞书应用 app id
FEISHU_APP_SECRET=飞书应用 secret
FEISHU_BITABLE_APP_TOKEN=多维表格 app token
FEISHU_BITABLE_TABLE_ID=表 table id

SUBSCRIPTION_COUPON_CODE=线上 Stripe 可用优惠券
REGISTER_DIR=/home/ec2-user/yuha/register

# 如果启用 HTTP API 服务必须设置
ONLINE_CHECK_API_TOKEN=强随机 token
ONLINE_CHECK_API_HOST=127.0.0.1
ONLINE_CHECK_API_PORT=8787
```

飞书应用权限至少需要：

- 发送消息
- 上传图片
- 多维表格读写
- 云文档/Drive 附件上传

## 6. 服务器冒烟验证

先只验证飞书单人发送，不跑全链路：

```bash
cd /home/ec2-user/yuha/online_check_py
.venv/bin/python demo_send_yangyuxia.py
```

发送最近一次 full 报告和失败截图：

```bash
.venv/bin/python demo_send_yangyuxia.py --latest-full
```

真正跑全链路调试，只发给杨玉霞：

```bash
.venv/bin/python demo_send_yangyuxia.py --run-full
```

等价 CLI：

```bash
.venv/bin/python -m src.index full --debug-recipient-email yangyuxia@vastai3d.com
```

## 7. 每天下午 6 点触发

建议用 systemd timer，而不是脚本内常驻 scheduler。

创建 service：

```bash
sudo tee /etc/systemd/system/online-check-full.service >/dev/null <<'EOF'
[Unit]
Description=Online Studio full check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/ec2-user/yuha/online_check_py
ExecStart=/home/ec2-user/yuha/online_check_py/.venv/bin/python -m src.index full
StandardOutput=append:/home/ec2-user/yuha/online_check_py/artifacts/online-check-full.log
StandardError=append:/home/ec2-user/yuha/online_check_py/artifacts/online-check-full.err.log
EOF
```

创建 timer，每天 Asia/Shanghai 18:00 触发。服务器如果使用 UTC，`OnCalendar` 可以显式写时区：

```bash
sudo tee /etc/systemd/system/online-check-full.timer >/dev/null <<'EOF'
[Unit]
Description=Run Online Studio full check every day at 18:00 Asia/Shanghai

[Timer]
OnCalendar=Asia/Shanghai *-*-* 18:00:00
Persistent=true
Unit=online-check-full.service

[Install]
WantedBy=timers.target
EOF
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now online-check-full.timer
systemctl list-timers online-check-full.timer
```

手动触发一次：

```bash
sudo systemctl start online-check-full.service
tail -n 200 /home/ec2-user/yuha/online_check_py/artifacts/online-check-full.log
tail -n 200 /home/ec2-user/yuha/online_check_py/artifacts/online-check-full.err.log
```

## 8. 可选 HTTP API 服务

如果需要外部手动触发，可额外部署 API 服务：

```bash
sudo tee /etc/systemd/system/online-check-api.service >/dev/null <<'EOF'
[Unit]
Description=Online Check API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ec2-user/yuha/online_check_py
ExecStart=/home/ec2-user/yuha/online_check_py/.venv/bin/python -m src.index api
Restart=always
RestartSec=10
StandardOutput=append:/home/ec2-user/yuha/online_check_py/artifacts/online-check-api.log
StandardError=append:/home/ec2-user/yuha/online_check_py/artifacts/online-check-api.err.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now online-check-api.service
```

调用：

```bash
curl -X POST http://127.0.0.1:8787/online-check/run \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${ONLINE_CHECK_API_TOKEN}" \
  -d '{"mode":"full"}'
```

API 服务当前是单实例内存任务状态，不要多副本部署。

## 9. 线上排查触发器

查看是否已经有部署或重复触发：

```bash
systemctl list-units --type=service | grep -E 'online-check|feishu|bug'
systemctl list-timers | grep -E 'online-check|feishu|bug'
crontab -l
sudo crontab -l
grep -R "online_check_py\\|src.index\\|feishu_bug_alert" /etc/cron* /var/spool/cron 2>/dev/null
ps -ef | grep -E 'online_check_py|src.index|feishu_bug_alert' | grep -v grep
```

注意：`feishu_bug_alert` 是另一个项目，当前日志显示它已有 systemd/调度痕迹，并且脚本内 APScheduler 是每天 14:00。若晚上 18:00 仍有提醒，需要重点查 crontab、systemd timer 或旧进程。

## 10. 运维关注点

- `SUBSCRIPTION_COUPON_CODE` 必须有效，否则订阅 case 会失败。
- `artifacts/` 会生成截图和 JSON，需要定期清理。
- 服务器必须能访问 `studio.tripo3d.ai`、`auth.tripo3d.ai`、`api.tripo3d.ai`、`checkout.stripe.com`、`www.otpebox.com`、`open.feishu.cn`。
- 如果 Playwright 启动失败，优先重跑 `.venv/bin/python -m playwright install --with-deps chromium`。
- 如果飞书发不出去，先跑 `.venv/bin/python demo_send_yangyuxia.py` 验证应用权限和网络。
