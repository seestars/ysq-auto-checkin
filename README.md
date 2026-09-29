# 源社区签到脚本

源社区（[src.top](https://src.top)）每日签到自动化脚本。基于 Discuz! X3.5 的 `k_misign` 签到插件，单文件 Python 实现，可直接用于本地 cron 或 GitHub Actions。

## 快速开始

```bash
pip install -r requirements.txt

# 方式 A：账号密码自动登录（免手动更新 Cookie，推荐）
export SRC_USERNAME='你的用户名'
export SRC_PASSWORD='你的密码'
python sign.py

# 方式 B：Cookie 登录
export SRC_COOKIE='YPSa_2132_auth=xxx; YPSa_2132_saltkey=yyy; ...'
python sign.py
```

或复制环境变量模板后用 `.env`：

```bash
cp config.example.env .env
# 编辑 .env 填入凭证
python sign.py
```

## 登录凭证

**Cookie 会过期吗？** 会。但脚本会**自动更新**：

1. 优先用 Cookie（Secrets 里的 `SRC_COOKIE` 或上次登录缓存）
2. Cookie 失效 → 自动改用账密登录拿全新 Cookie
3. 新 Cookie 写入缓存（本地 `.src_cookie_cache`；GitHub Actions 用 cache 持久化）
4. 下次运行优先用新 Cookie，不必反复走登录

**推荐配置（GitHub Actions）**：同时配 `SRC_USERNAME` + `SRC_PASSWORD`（必配）与 `SRC_COOKIE`（可选种子）。Secrets 里的 Cookie 过期后无需手动更新。

| 方式 | 变量 | 说明 |
|---|---|---|
| **账号密码（推荐）** | `SRC_USERNAME` + `SRC_PASSWORD` | 自动登录，成功后缓存 Cookie 供下次复用 |
| Cookie | `SRC_COOKIE` | 浏览器复制，方便但会过期 |
| Cookie 文件 | `SRC_COOKIE_FILE` | Netscape `cookies.txt` 或原始 Cookie 头 |
| Cookie 缓存 | 自动 | 路径 `.src_cookie_cache`，账密登录成功后自动写入 |

**认证顺序**：配置 Cookie → 缓存 Cookie → 账密登录（成功后更新缓存）。

**安全提问**（若账号设置了）：额外配置 `SRC_QUESTION_ID`（1–7，见登录页下拉框）和 `SRC_ANSWER`。未设置则不用管。

| ID | 提问 |
|---|---|
| 0 | 无（默认） |
| 1 | 母亲的名字 |
| 2 | 爷爷的名字 |
| 3 | 父亲出生的城市 |
| 4 | 您其中一位老师的名字 |
| 5 | 您个人计算机的型号 |
| 6 | 您最喜欢的餐馆名称 |
| 7 | 驾驶执照最后四位数字 |

**Cookie 获取**（可选）：浏览器登录 https://src.top → F12 → Network → 任意请求 → Request Headers → 复制整行 `Cookie:` 后的值（须含 `YPSa_2132_auth`）。

> **注意**：若站点对密码登录启用了验证码，账密方式会被拦（退出码 3）。此时只能用 Cookie，或降低登录频率。先本地跑一次 `python sign.py --dry-run` 验证账密是否可用。

## 命令行参数

环境变量是主配置（适合 GitHub Actions Secrets），CLI 可覆盖：

```bash
python sign.py --cookie "YPSa_2132_auth=..." --dry-run --log-level DEBUG
python sign.py --username myuser --password mypass
python sign.py --base-url https://src.top
```

优先级：**CLI > 环境变量 > 默认值**。

## 环境变量

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `SRC_USERNAME` | 账密登录 | — | 用户名 |
| `SRC_PASSWORD` | 账密登录 | — | 密码 |
| `SRC_QUESTION_ID` | 否 | `0` | 安全提问 ID（1–7），未设置则不用 |
| `SRC_ANSWER` | 否 | — | 安全提问答案 |
| `SRC_COOKIE` | 否 | — | 完整 Cookie 头（与账密同时配可自动回退） |
| `SRC_COOKIE_FILE` | 否 | — | Cookie 文件路径 |
| `SRC_BASE_URL` | 否 | `https://src.top` | 站点地址 |
| `SRC_TIMEOUT` | 否 | `30` | 请求超时（秒） |
| `SRC_RETRIES` | 否 | `3` | 网络重试次数 |
| `SRC_RETRY_BACKOFF` | 否 | `2` | 重试退避基数（秒） |
| `SRC_DRY_RUN` | 否 | `0` | 设 `1` 时只登录不签到 |
| `SRC_LOG_LEVEL` | 否 | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `SRC_USER_AGENT` | 否 | Chrome UA | 自定义 UA |
| `SRC_TG_BOT_TOKEN` | 否 | — | Telegram Bot Token，配置后推送签到结果 |
| `SRC_TG_CHAT_ID` | 否 | — | Telegram Chat ID |

## Telegram 推送（可选）

签到结果自动推送到 Telegram（成功 ☑️ / 已签 ✅ / 失败 ❌），便于第一时间发现 Cookie 失效或登录被拦。

**登录失败会单独告警**（🚨 源社区签到 · 登录失败），并附排查建议：

### 获取 Bot Token 与 Chat ID

1. Telegram 搜索 **@BotFather** → `/newbot` → 得到 `Bot Token`（形如 `123456789:ABCdef...`）
2. 搜索你刚创建的 bot，**随便发一条消息**（如 `hi`）
3. 浏览器打开 `https://api.telegram.org/bot<你的TOKEN>/getUpdates`，在返回 JSON 里找 `"chat":{"id":123456789}`，即为 `Chat ID`

### 配置

```bash
export SRC_TG_BOT_TOKEN='123456789:ABCdef...'
export SRC_TG_CHAT_ID='123456789'
```

GitHub Secrets 同名添加 `SRC_TG_BOT_TOKEN`、`SRC_TG_CHAT_ID` 即可。

> 推送失败只记 WARNING，不影响签到退出码。dry-run 不推送。日志自动脱敏 bot token。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 签到成功 **或** 今日已签 |
| 1 | 签到请求失败 |
| 2 | 配置错误（缺凭证等） |
| 3 | 认证失败（Cookie 失效 / 登录被拒 / 要验证码） |
| 4 | formhash 提取失败 |
| 5 | 网络重试耗尽 |

## 工作原理

1. **认证**：Cookie 优先；失败或无 Cookie 时回退账号密码登录。
2. **取 formhash**：从签到页提取 Discuz CSRF token（8 位 hex）。
3. **签到**：`GET /plugin.php?id=k_misign:sign&operation=qiandao&formhash=<hash>&format=empty`。
4. **判定**：按响应关键词识别成功 / 已签 / 未登录 / 失败；空响应时回读签到页复验。

## 测试

```bash
# A. 无配置 → 退出码 2
unset SRC_COOKIE SRC_USERNAME SRC_PASSWORD
python sign.py

# B. Cookie dry-run（只登录，不签到）
export SRC_COOKIE='...'
python sign.py --dry-run --log-level DEBUG

# C. 真实签到
python sign.py
# 再跑一次 → already_signed，仍退出码 0

# E. 网络失败 → 退出码 5
SRC_BASE_URL=https://127.0.0.1:9 python sign.py --cookie dummy
```

## 安全提示

- 切勿将 `.env` / Cookie / 密码提交到公开仓库（`.env` 已建议加入 `.gitignore`）。
- GitHub Actions 请使用 **Secrets** 注入环境变量。
- 日志会自动脱敏 Cookie 与密码字段。

## GitHub Actions

workflow 位于 `.github/workflows/sign.yml`，每天 **北京时间 09:00**（UTC 01:00）自动签到，也可在 Actions 页手动触发（`workflow_dispatch`）。

### 配置 Secrets

仓库 → **Settings → Secrets and variables → Actions → New repository secret**：

| Secret 名 | 必需 | 值 |
|---|---|---|
| `SRC_USERNAME` | 是（推荐） | 论坛用户名 |
| `SRC_PASSWORD` | 是（推荐） | 论坛密码 |
| `SRC_QUESTION_ID` | 否 | 安全提问 ID（1–7），账号未设置则省略 |
| `SRC_ANSWER` | 否 | 安全提问答案 |
| `SRC_COOKIE` | 否 | Cookie 头，可选作为主登录方式或与账密同时配 |
| `SRC_TG_BOT_TOKEN` | 否 | Telegram Bot Token（推送签到结果） |
| `SRC_TG_CHAT_ID` | 否 | Telegram Chat ID |

**推荐配置**：`SRC_USERNAME` + `SRC_PASSWORD` 必配；`SRC_COOKIE` 可选（仅作首次种子，过期后无需手动更新）。

`SRC_COOKIE` 不配也能跑——脚本会用账密登录并把 Cookie 缓存到 Actions cache，下次优先用缓存 Cookie。Secrets 里的 Cookie 过期后无需手动更新。

若担心密码登录触发验证码，可同时加 `SRC_COOKIE`：脚本优先用 Cookie，过期后自动回退账密登录。

### 调整签到时间

改 `sign.yml` 里的 cron 表达式（**UTC 时区**，北京时间需减 8 小时）：

| 北京时间 | cron |
|---|---|
| 08:00 | `0 0 * * *` |
| 09:00 | `0 1 * * *` |
| 12:00 | `0 4 * * *` |
| 20:00 | `0 12 * * *` |

> GitHub 官方调度可能有几分钟延迟；高峰时段（整点）延迟更明显，可故意错峰（如 `7 1 * * *`）。

## 免责声明

本脚本仅供个人学习与自动化使用。使用产生的任何后果由使用者自行承担。
