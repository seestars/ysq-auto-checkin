#!/usr/bin/env python3
"""源社区 (src.top) 每日签到脚本 — Discuz! X3.5 + k_misign。

用法:
    export SRC_COOKIE='YPSa_2132_auth=...; YPSa_2132_saltkey=...'
    python sign.py

退出码: 0 成功/已签 1 签到失败 2 配置错误 3 认证失败 4 formhash失败 5 网络失败
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://src.top"
PATH_LOGIN_PAGE = "/member.php?mod=logging&action=login"
PATH_LOGIN_SUBMIT = "/member.php?mod=logging&action=login&loginsubmit=yes&inajax=1"
PATH_SIGN_PAGE = "/k_misign-sign.html"
PATH_SIGN_API = (
    "/plugin.php?id=k_misign:sign&operation=qiandao"
    "&formhash={formhash}&format=empty"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 密码登录成功后自动保存 Cookie，供下次优先复用
DEFAULT_COOKIE_CACHE = ".src_cookie_cache"

log = logging.getLogger("src.sign")

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """配置错误 → 退出码 2"""


class AuthError(Exception):
    """认证失败 → 退出码 3"""


class FormhashError(Exception):
    """formhash 提取失败 → 退出码 4"""


class SignError(Exception):
    """签到请求失败 → 退出码 1"""


class NetworkError(Exception):
    """网络重试耗尽 → 退出码 5"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Account:
    name: str
    cookie_header: str | None = None
    username: str | None = None
    password: str | None = None
    questionid: str = "0"
    answer: str = ""


@dataclass
class SignResult:
    status: str  # success | already_signed | not_logged_in | failed
    message: str
    http_status: int


@dataclass
class Options:
    base_url: str = DEFAULT_BASE_URL
    dry_run: bool = False
    timeout: float = 30.0
    retries: int = 3
    retry_backoff: float = 2.0
    user_agent: str = DEFAULT_USER_AGENT
    tg_bot_token: str | None = None
    tg_chat_id: str | None = None
    cookie_cache_file: str = DEFAULT_COOKIE_CACHE


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def _load_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析（不覆盖已存在的环境变量）。"""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("'\"")
    return result


def parse_cookie_file(path: Path) -> str:
    """读取 Cookie：优先按 Netscape cookies.txt，否则按原始 Cookie 头。"""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ConfigError(f"Cookie 文件为空: {path}")
    # Netscape: 每行 7 列 tab 分隔，域名开头可带点
    if "\t" in text and not text.lower().startswith("cookie:"):
        pairs = []
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                pairs.append(f"{parts[5]}={parts[6]}")
        if pairs:
            return "; ".join(pairs)
    # 原始 Cookie: header 或 k=v; k2=v2
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    return text


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_config(argv: list[str] | None = None) -> tuple[list[Account], Options]:
    """CLI > 环境变量 > 默认值。返回 (accounts, options)。"""
    dotenv = _load_dotenv(Path(".env"))

    def env(key: str) -> str | None:
        if key in os.environ:
            return os.environ[key]
        return dotenv.get(key)

    parser = argparse.ArgumentParser(
        description="源社区 (src.top) 每日签到",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--cookie", help="Cookie 头（覆盖 SRC_COOKIE）")
    parser.add_argument("--cookie-file", help="Cookie 文件路径（覆盖 SRC_COOKIE_FILE）")
    parser.add_argument("--username", help="用户名（覆盖 SRC_USERNAME）")
    parser.add_argument("--password", help="密码（覆盖 SRC_PASSWORD）")
    parser.add_argument("--questionid", help="安全提问 ID（覆盖 SRC_QUESTION_ID），0=无")
    parser.add_argument("--answer", help="安全提问答案（覆盖 SRC_ANSWER）")
    parser.add_argument("--base-url", help=f"站点地址（覆盖 SRC_BASE_URL），默认 {DEFAULT_BASE_URL}")
    parser.add_argument("--dry-run", action="store_true", help="只登录取 formhash，不真正签到")
    parser.add_argument("--log-level", help="DEBUG/INFO/WARNING，默认 INFO")
    parser.add_argument("--tg-bot-token", help="Telegram Bot Token（覆盖 SRC_TG_BOT_TOKEN）")
    parser.add_argument("--tg-chat-id", help="Telegram Chat ID（覆盖 SRC_TG_CHAT_ID）")
    args = parser.parse_args(argv)

    cookie = args.cookie or env("SRC_COOKIE")
    cookie_file = args.cookie_file or env("SRC_COOKIE_FILE")
    username = args.username or env("SRC_USERNAME")
    password = args.password or env("SRC_PASSWORD")
    questionid = args.questionid or env("SRC_QUESTION_ID") or "0"
    answer = args.answer or env("SRC_ANSWER") or ""

    if not cookie and cookie_file:
        cookie = parse_cookie_file(Path(cookie_file))

    # Cookie 与账密可同时存在：Cookie 优先，失效时自动回退账密登录
    if cookie and username and password:
        account = Account(
            name=f"{username}+cookie",
            cookie_header=cookie,
            username=username,
            password=password,
            questionid=questionid,
            answer=answer,
        )
    elif cookie:
        account = Account(name="cookie", cookie_header=cookie)
    elif username and password:
        account = Account(
            name=username,
            username=username,
            password=password,
            questionid=questionid,
            answer=answer,
        )
    else:
        raise ConfigError(
            "缺少登录凭证。请提供以下任一方式:\n"
            "  1) SRC_USERNAME + SRC_PASSWORD — 账号密码自动登录（免手动更新 Cookie，推荐）\n"
            "  2) SRC_COOKIE  — 浏览器复制的 Cookie 头（含 YPSa_2132_auth）\n"
            "  3) SRC_COOKIE_FILE — Cookie 文件路径\n"
            "  也可同时配置 Cookie + 账密：Cookie 优先，过期自动回退账密登录。"
        )

    base_url = (args.base_url or env("SRC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    log_level = (args.log_level or env("SRC_LOG_LEVEL") or "INFO").upper()
    timeout = float(env("SRC_TIMEOUT") or 30)
    retries = int(env("SRC_RETRIES") or 3)
    backoff = float(env("SRC_RETRY_BACKOFF") or 2)
    dry_run = args.dry_run or _truthy(env("SRC_DRY_RUN"))
    ua = env("SRC_USER_AGENT") or DEFAULT_USER_AGENT
    tg_bot_token = args.tg_bot_token or env("SRC_TG_BOT_TOKEN")
    tg_chat_id = args.tg_chat_id or env("SRC_TG_CHAT_ID")
    cookie_cache_file = env("SRC_COOKIE_CACHE_FILE") or DEFAULT_COOKIE_CACHE

    options = Options(
        base_url=base_url,
        dry_run=dry_run,
        timeout=timeout,
        retries=retries,
        retry_backoff=backoff,
        user_agent=ua,
        tg_bot_token=tg_bot_token or None,
        tg_chat_id=tg_chat_id or None,
        cookie_cache_file=cookie_cache_file,
    )
    # 让 main() 能拿到日志级别
    options.log_level = log_level  # type: ignore[attr-defined]
    return [account], options


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def build_session(account: Account, options: Options) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": options.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
    )
    if account.cookie_header:
        # 直接注入原始 Cookie 头，保留 Discuz cookie 原值
        session.headers["Cookie"] = account.cookie_header
    return session


def request_with_retry(
    session: requests.Session, method: str, url: str, options: Options, **kwargs
) -> requests.Response:
    """带重试的请求。仅重试网络错误与 5xx。"""
    kwargs.setdefault("timeout", options.timeout)
    last_exc: Exception | None = None
    for attempt in range(options.retries + 1):
        try:
            resp = session.request(method, url, **kwargs)
            if resp.status_code in (502, 503, 529) and attempt < options.retries:
                raise requests.exceptions.ConnectionError(f"HTTP {resp.status_code}")
            resp.encoding = "utf-8"
            return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < options.retries:
                wait = options.retry_backoff * (2**attempt)
                log.warning("请求失败(%s)，%.1f 秒后重试 %d/%d: %s",
                            exc.__class__.__name__, wait, attempt + 1, options.retries, exc)
                time.sleep(wait)
            else:
                break
    raise NetworkError(f"网络请求失败（已重试 {options.retries} 次）: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

_FORMHASH_PATTERNS = [
    re.compile(r"""name=["']formhash["']\s+value=["']([a-f0-9]{8})["']"""),
    re.compile(r"""value=["']([a-f0-9]{8})["']\s+name=["']formhash["']"""),
    re.compile(r"""formhash=([a-f0-9]{8})"""),
    re.compile(r"""formhash["']?\s*[:=]\s*["']([a-f0-9]{8})["']"""),
]


def extract_formhash(html: str) -> str:
    for pattern in _FORMHASH_PATTERNS:
        m = pattern.search(html)
        if m:
            return m.group(1)
    raise FormhashError("无法从页面提取 formhash（可能未登录或页面结构变化）")


def is_logged_in(html: str) -> bool:
    if "请先登录" in html or "需要登录" in html:
        return False
    # 登录页会出现登录表单
    if 'id="loginform' in html or "loginform_Lw" in html:
        return False
    try:
        extract_formhash(html)
    except FormhashError:
        return False
    # 已登录页面会有退出链接
    if "action=logout" in html or "退出" in html:
        return True
    return True


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------


def load_cookie_cache(options: Options) -> str | None:
    """读取上次密码登录保存的 Cookie 头。"""
    path = Path(options.cookie_cache_file)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def save_cookie_cache(options: Options, cookie_header: str) -> None:
    """把当前会话 Cookie 落盘，下次优先复用（GitHub Actions 用 cache 持久化）。"""
    path = Path(options.cookie_cache_file)
    try:
        path.write_text(cookie_header, encoding="utf-8")
        log.info("Cookie 已缓存到 %s（下次优先使用）", path)
    except OSError as exc:
        log.warning("写入 Cookie 缓存失败: %s", exc)


def session_cookie_header(session: requests.Session) -> str:
    """把 session cookie jar 序列化为 Cookie 头字符串。"""
    return "; ".join(f"{c.name}={c.value}" for c in session.cookies)


def _apply_cookie(session: requests.Session, cookie_header: str) -> None:
    # 清掉旧的，避免手动 Header 与 jar 混用导致带上过期值
    session.cookies.clear()
    session.headers["Cookie"] = cookie_header


def authenticate_with_cookie(
    session: requests.Session, account: Account, options: Options
) -> str:
    resp = request_with_retry(
        session,
        "GET",
        f"{options.base_url}{PATH_SIGN_PAGE}",
        options,
        headers={"Referer": f"{options.base_url}/"},
    )
    html = resp.text
    if not is_logged_in(html):
        raise AuthError("Cookie 无效或已过期（页面显示未登录）")
    formhash = extract_formhash(html)
    log.info("Cookie 认证成功，formhash=%s", formhash)
    return formhash


def authenticate_with_password(
    session: requests.Session, account: Account, options: Options
) -> str:
    login_url = f"{options.base_url}{PATH_LOGIN_PAGE}"
    resp = request_with_retry(
        session, "GET", login_url, options, headers={"Referer": f"{options.base_url}/"}
    )
    formhash = extract_formhash(resp.text)

    submit_url = f"{options.base_url}{PATH_LOGIN_SUBMIT}"
    data = {
        "formhash": formhash,
        "username": account.username or "",
        "password": account.password or "",
        "loginfield": "username",
        "cookietime": "2592000",
        "handlekey": "ls",
        "quickforward": "yes",
        "lssubmit": "yes",
        "questionid": account.questionid,
        "answer": account.answer,
        "referer": f"{options.base_url}{PATH_SIGN_PAGE}",
    }
    resp = request_with_retry(
        session,
        "POST",
        submit_url,
        options,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": login_url,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    body = resp.text

    for marker in ("验证码", "安全提问", "seccode", "secqaa"):
        if marker in body and "succeedhandle" not in body:
            raise AuthError("登录需要验证码/安全提问，请改用 Cookie 认证（SRC_COOKIE）")

    for marker in ("密码错误", "登录失败", "错误的密码", "错误信息", "失败"):
        if marker in body and "succeedhandle" not in body and "欢迎回来" not in body:
            snippet = re.sub(r"\s+", " ", body)[:200]
            raise AuthError(f"登录失败: {snippet}")

    # 复验：以签到页为准
    probe = request_with_retry(
        session,
        "GET",
        f"{options.base_url}{PATH_SIGN_PAGE}",
        options,
        headers={"Referer": f"{options.base_url}/"},
    )
    if not is_logged_in(probe.text):
        snippet = re.sub(r"\s+", " ", body)[:200]
        raise AuthError(f"登录后仍未登录（响应片段: {snippet}）")
    formhash = extract_formhash(probe.text)
    log.info("密码登录成功，formhash=%s", formhash)
    return formhash


def authenticate(
    session: requests.Session, account: Account, options: Options
) -> str:
    """认证顺序：显式 Cookie → 缓存 Cookie → 账密登录。

    账密登录成功后会把新 Cookie 写入缓存，下次优先用 Cookie，免重复登录。
    """
    cookie_sources: list[tuple[str, str]] = []
    if account.cookie_header:
        cookie_sources.append(("配置 Cookie", account.cookie_header))
    cached = load_cookie_cache(options)
    if cached and cached != account.cookie_header:
        cookie_sources.append(("缓存 Cookie", cached))

    for label, cookie_header in cookie_sources:
        try:
            _apply_cookie(session, cookie_header)
            log.info("使用 %s 认证", label)
            formhash = authenticate_with_cookie(session, account, options)
            # 认证成功后同步缓存，保持 Cookie 最新
            save_cookie_cache(options, session_cookie_header(session) or cookie_header)
            return formhash
        except AuthError as exc:
            log.warning("%s 认证失败: %s", label, exc)
            session.headers.pop("Cookie", None)
            session.cookies.clear()

    if account.username and account.password:
        if cookie_sources:
            log.info("Cookie 均已失效，使用账号密码登录并更新 Cookie")
        else:
            log.info("使用账号密码登录")
        formhash = authenticate_with_password(session, account, options)
        # 自动更新 Cookie 缓存，下次优先走 Cookie
        header = session_cookie_header(session)
        if header:
            save_cookie_cache(options, header)
        return formhash

    raise AuthError("无可用登录凭证（Cookie 失效且未配置账密）")


# ---------------------------------------------------------------------------
# 签到
# ---------------------------------------------------------------------------

_SUCCESS_MARKERS = ("签到成功", "恭喜您签到成功", "签到成功！")
_ALREADY_MARKERS = ("今日已签", "已签到", "已经签到", "重复签到", "您今天已经签到")
_NOT_LOGGED_IN_MARKERS = ("请先登录", "未登录", "需要登录")
_CHALLENGE_MARKERS = ("验证码", "安全提问", "seccode", "secqaa")
_FAIL_MARKERS = ("没有权限", "禁止", "失败", "错误")


def classify_sign_response(body: str, http_status: int) -> SignResult:
    text = re.sub(r"\s+", " ", body).strip()
    snippet = text[:200]

    if any(m in text for m in _NOT_LOGGED_IN_MARKERS):
        return SignResult("not_logged_in", snippet, http_status)
    if any(m in text for m in _SUCCESS_MARKERS):
        return SignResult("success", snippet, http_status)
    if any(m in text for m in _ALREADY_MARKERS):
        return SignResult("already_signed", snippet, http_status)
    if any(m in text for m in _CHALLENGE_MARKERS):
        return SignResult("failed", f"需要验证: {snippet}", http_status)
    if any(m in text for m in _FAIL_MARKERS):
        return SignResult("failed", snippet, http_status)
    if http_status == 200 and not text:
        return SignResult("success", "(空响应，待复验)", http_status)
    return SignResult("failed", f"未识别响应(HTTP {http_status}): {snippet}", http_status)


def confirm_signed(session: requests.Session, options: Options) -> bool:
    """空响应兜底：回读签到页确认是否已签。"""
    resp = request_with_retry(
        session,
        "GET",
        f"{options.base_url}{PATH_SIGN_PAGE}",
        options,
        headers={"Referer": f"{options.base_url}/"},
    )
    html = resp.text
    if any(m in html for m in _ALREADY_MARKERS):
        return True
    if any(m in html for m in _SUCCESS_MARKERS):
        return True
    # 「连续签到」通常表示已签状态区块
    if "连续签到" in html and "您今天还没有签到" not in html:
        return True
    return False


def perform_sign(
    session: requests.Session, formhash: str, options: Options
) -> SignResult:
    sign_url = options.base_url + PATH_SIGN_API.format(formhash=formhash)
    headers = {
        "Referer": f"{options.base_url}{PATH_SIGN_PAGE}",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
    }
    log.info("请求签到: GET %s", sign_url)
    resp = request_with_retry(session, "GET", sign_url, options, headers=headers)

    # GET 不被接受时降级 POST
    if resp.status_code == 405:
        log.info("GET 被拒(405)，降级 POST")
        resp = request_with_retry(session, "POST", sign_url, options, headers=headers)

    result = classify_sign_response(resp.text, resp.status_code)
    log.info("签到响应(HTTP %d): %s", result.http_status, result.message)

    # 空响应 + 200 → 复验
    if result.status == "success" and "(空响应" in result.message:
        if confirm_signed(session, options):
            return SignResult("success", "签到成功（空响应已复验）", result.http_status)
        return SignResult("failed", "空响应且复验未发现已签标记", result.http_status)

    # formhash 失效时重新取一次再签
    if result.status == "failed" and ("formhash" in result.message.lower() or "非法" in result.message or "无效" in result.message):
        log.warning("formhash 可能失效，重新获取后重试一次")
        page = request_with_retry(
            session,
            "GET",
            f"{options.base_url}{PATH_SIGN_PAGE}",
            options,
            headers={"Referer": f"{options.base_url}/"},
        )
        try:
            new_hash = extract_formhash(page.text)
        except FormhashError:
            return result
        if new_hash != formhash:
            return perform_sign(session, new_hash, options)

    return result


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------


def redact(text: str) -> str:
    text = re.sub(r"(YPSa_\w+_auth=)[^;\s]+", r"\1***", text)
    text = re.sub(r"(password=)[^&\s]+", r"\1***", text, flags=re.I)
    text = re.sub(r"(Cookie:\s*)[^\n]+", r"\1***", text)
    # Telegram bot token: 数字:字母数字 串
    text = re.sub(r"bot\d+:[A-Za-z0-9_-]{20,}", "bot***", text)
    return text


def run_account(account: Account, options: Options) -> SignResult:
    session = build_session(account, options)
    log.info("账号: %s | 模式: %s | 目标: %s",
             account.name, "dry-run" if options.dry_run else "live", options.base_url)

    formhash = authenticate(session, account, options)

    if options.dry_run:
        sign_url = options.base_url + PATH_SIGN_API.format(formhash=formhash)
        log.info("[dry-run] 将签到: GET %s", sign_url)
        return SignResult("success", "dry-run", 200)

    return perform_sign(session, formhash, options)


def result_to_exit_code(result: SignResult) -> int:
    if result.status in ("success", "already_signed"):
        return 0
    if result.status == "not_logged_in":
        return 3
    return 1


# ---------------------------------------------------------------------------
# Telegram 推送
# ---------------------------------------------------------------------------

_TG_ICON = {
    "success": "✅",
    "already_signed": "☑️",
    "not_logged_in": "🚨",
    "auth_error": "🚨",
    "config_error": "⚠️",
    "formhash_error": "🚨",
    "network_error": "📡",
    "sign_error": "❌",
    "failed": "❌",
}

_TG_STATUS_TEXT = {
    "success": "签到成功",
    "already_signed": "今日已签到",
    "not_logged_in": "登录失败",
    "auth_error": "登录失败",
    "config_error": "配置错误",
    "formhash_error": "登录异常",
    "network_error": "网络异常",
    "sign_error": "签到失败",
    "failed": "签到失败",
}

# 登录/认证类失败时附加的排查提示
_TG_AUTH_HINTS = (
    "排查建议:",
    "1. 检查 SRC_USERNAME / SRC_PASSWORD 是否正确",
    "2. 检查 SRC_COOKIE 是否过期（若已配）",
    "3. 账号若设置了安全提问，需配 SRC_QUESTION_ID + SRC_ANSWER",
    "4. 密码登录若被验证码拦截，请改用 Cookie 登录",
)


def notify_telegram(options: Options, title: str, text: str) -> None:
    """发送 Telegram 通知。失败只记日志，不影响签到退出码。"""
    token = options.tg_bot_token
    chat_id = options.tg_chat_id
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"{title}\n{text}",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=options.timeout)
        if resp.status_code == 200:
            log.info("Telegram 推送成功")
        else:
            snippet = re.sub(r"\s+", " ", resp.text)[:150]
            log.warning("Telegram 推送失败(HTTP %d): %s", resp.status_code, snippet)
    except requests.exceptions.RequestException as exc:
        log.warning("Telegram 推送异常: %s", redact(str(exc)))


def build_notify_message(
    account: Account,
    result: SignResult | None,
    error: str | None = None,
    error_kind: str | None = None,
) -> tuple[str, str]:
    """返回 (title, body)。

    error_kind: auth_error / config_error / formhash_error / network_error / sign_error
    """
    if result and result.status in ("success", "already_signed"):
        status_key = result.status
    elif result and result.status == "not_logged_in":
        status_key = "not_logged_in"
    elif error_kind:
        status_key = error_kind
    elif result:
        status_key = result.status
    else:
        status_key = "failed"

    icon = _TG_ICON.get(status_key, "❌")
    status_text = _TG_STATUS_TEXT.get(status_key, "未知")
    title = f"{icon} 源社区签到 · {status_text}"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"账号: {account.name}",
        f"时间: {now}",
    ]
    if result:
        lines.append(f"详情: {redact(result.message)[:180]}")
    if error:
        lines.append(f"错误: {redact(error)[:180]}")
    # 登录/认证类失败加排查提示
    if status_key in ("not_logged_in", "auth_error", "formhash_error"):
        lines.append("")
        lines.extend(_TG_AUTH_HINTS)
    return title, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    try:
        accounts, options = load_config(argv)
    except ConfigError as exc:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        log.error("配置错误: %s", exc)
        return 2
    except SystemExit as exc:
        # argparse 错误
        return int(exc.code or 2)

    log_level = getattr(options, "log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, str(log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    account = accounts[0]
    result: SignResult | None = None
    error: str | None = None
    error_kind: str | None = None
    exit_code = 1

    try:
        result = run_account(account, options)
        exit_code = result_to_exit_code(result)
    except ConfigError as exc:
        error = str(exc)
        error_kind = "config_error"
        log.error("配置错误: %s", exc)
        exit_code = 2
    except AuthError as exc:
        error = str(exc)
        error_kind = "auth_error"
        log.error("登录失败: %s", exc)
        exit_code = 3
    except FormhashError as exc:
        error = str(exc)
        error_kind = "formhash_error"
        log.error("formhash 失败: %s", exc)
        exit_code = 4
    except NetworkError as exc:
        error = str(exc)
        error_kind = "network_error"
        log.error("网络失败: %s", exc)
        exit_code = 5
    except SignError as exc:
        error = str(exc)
        error_kind = "sign_error"
        log.error("签到失败: %s", exc)
        exit_code = 1
    except KeyboardInterrupt:
        log.error("用户中断")
        return 130

    if result and exit_code == 0:
        log.info("完成: %s | %s", result.status, redact(result.message))
    elif result:
        log.error("失败(%s): %s", result.status, redact(result.message))

    # dry-run 不推送；其余情况（含登录失败）均推送告警
    if not options.dry_run:
        title, body = build_notify_message(account, result, error, error_kind)
        notify_telegram(options, title, body)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
