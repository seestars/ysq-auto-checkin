# 源社区签到脚本

源社区（[src.top](https://src.top)）每日签到自动化脚本。基于 Discuz! X3.5 的 `k_misign` 签到插件，单文件 Python 实现，可直接用于本地 cron 或 GitHub Actions。

## 快速开始

```bash
pip install -r requirements.txt

# 设置 Cookie（推荐）
export SRC_COOKIE='YPSa_2132_auth=xxx; YPSa_2132_saltkey=yyy; ...'
python sign.py
```

或复制环境变量模板后用 `.env`：

```bash
cp config.example.env .env
# 编辑 .env 填入 SRC_COOKIE
python sign.py
```

## 登录凭证

三种方式，按优先级：

| 方式 | 变量 | 说明 |
|---|---|---|
| **Cookie（推荐）** | `SRC_COOKIE` | 浏览器登录后复制 Cookie 头，须含 `YPSa_2132_auth` 与 `YPSa_2132_saltkey` |
| Cookie 文件 | `SRC_COOKIE_FILE` | Netscape `cookies.txt` 或原始 Cookie 头文件 |
| 账号密码 | `SRC_USERNAME` + `SRC_PASSWORD` | 可能触发安全提问/验证码，不推荐长期使用 |

**如何获取 Cookie**：浏览器登录 https://src.top → F12 → Network → 刷新页面 → 任意请求 → Request Headers → 复制整行 `Cookie:` 后的值。

> Cookie 会过期。失效时脚本退出码为 3，需重新复制。

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
| `SRC_COOKIE` | 首选 | — | 完整 Cookie 头 |
| `SRC_COOKIE_FILE` | 否 | — | Cookie 文件路径 |
| `SRC_USERNAME` | 密码登录 | — | 用户名 |
| `SRC_PASSWORD` | 密码登录 | — | 密码 |
| `SRC_BASE_URL` | 否 | `https://src.top` | 站点地址 |
| `SRC_TIMEOUT` | 否 | `30` | 请求超时（秒） |
| `SRC_RETRIES` | 否 | `3` | 网络重试次数 |
| `SRC_RETRY_BACKOFF` | 否 | `2` | 重试退避基数（秒） |
| `SRC_DRY_RUN` | 否 | `0` | 设 `1` 时只登录不签到 |
| `SRC_LOG_LEVEL` | 否 | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `SRC_USER_AGENT` | 否 | Chrome UA | 自定义 UA |

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
| `SRC_COOKIE` | 是 | 浏览器复制的完整 Cookie 头（含 `YPSa_2132_auth`） |
| `SRC_USERNAME` | 否 | 回退用用户名 |
| `SRC_PASSWORD` | 否 | 回退用密码 |

只配 `SRC_COOKIE` 即可；账号密码仅作 Cookie 失效时的备用。

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
