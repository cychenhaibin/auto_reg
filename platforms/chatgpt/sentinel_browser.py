"""Playwright 版 Sentinel SDK token 获取辅助。"""

from __future__ import annotations

import json
from typing import Callable, Optional

from core.proxy_utils import build_playwright_proxy_config


def _flow_page_url(flow: str) -> str:
    flow_name = str(flow or "").strip().lower()
    mapping = {
        "authorize_continue": "https://auth.openai.com/create-account",
        "username_password_create": "https://auth.openai.com/create-account/password",
        "password_verify": "https://auth.openai.com/log-in/password",
        "email_otp_validate": "https://auth.openai.com/email-verification",
        "oauth_create_account": "https://auth.openai.com/about-you",
    }
    return mapping.get(flow_name, "https://auth.openai.com/about-you")


def get_sentinel_token_via_browser(
    *,
    flow: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    page_url: Optional[str] = None,
    headless: bool = True,
    device_id: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    return_cookies_dict: Optional[dict] = None,
) -> Optional[str]:
    """通过浏览器直接调用 SentinelSDK.token(flow) 获取完整 token。
    
    Args:
        return_cookies_dict: 如果传入一个 dict，会在获取 token 后将浏览器 cookies 写入其中，
                             方便调用方将浏览器会话 cookie 同步到 HTTP 客户端会话。
    """
    logger = log_fn or (lambda _msg: None)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        logger(f"Sentinel Browser 不可用: {e}")
        return None

    target_url = str(page_url or _flow_page_url(flow)).strip() or _flow_page_url(flow)
    launch_args = {
        "headless": bool(headless),
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
            "--disable-web-security",
            "--disable-features=VizDisplayCompositor",
            "--enable-features=NetworkService,NetworkServiceInProcess",
            "--disable-features=TranslateUI",
            "--ignore-certificate-errors",
            "--disable-features=BlockInsecurePrivateNetworkRequests",
        ],
    }
    proxy_config = build_playwright_proxy_config(proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config

    logger(f"Sentinel Browser 启动: flow={flow}, url={target_url}, headless={headless}")
    logger(f"Sentinel Browser 启动参数: {launch_args}")

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.7103.92 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            if device_id:
                try:
                    context.add_cookies(
                        [
                            {
                                "name": "oai-did",
                                "value": str(device_id),
                                "url": "https://auth.openai.com/",
                                "path": "/",
                                "secure": True,
                                "sameSite": "Lax",
                            }
                        ]
                    )
                except Exception:
                    pass

            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_function(
                "() => typeof window.SentinelSDK !== 'undefined' && typeof window.SentinelSDK.token === 'function'",
                timeout=min(timeout_ms, 45000),
            )

            result = page.evaluate(
                """
                async ({ flow }) => {
                    try {
                        const token = await window.SentinelSDK.token(flow);
                        return { success: true, token };
                    } catch (e) {
                        return {
                            success: false,
                            error: (e && (e.message || String(e))) || "unknown",
                        };
                    }
                }
                """,
                {"flow": flow},
            )

            if not result or not result.get("success") or not result.get("token"):
                logger(
                    "Sentinel Browser 获取失败: "
                    + str((result or {}).get("error") or "no result")
                )
                return None

            token = str(result["token"] or "").strip()
            if not token:
                logger("Sentinel Browser 返回空 token")
                return None

            # 提取浏览器 cookies 供调用方同步到 HTTP 会话
            if isinstance(return_cookies_dict, dict):
                try:
                    browser_cookies = context.cookies()
                    for c in browser_cookies:
                        return_cookies_dict[c.get("name", "")] = c.get("value", "")
                    logger(
                        f"Sentinel Browser cookies 提取: "
                        + ", ".join(f"{k}={v[:20]}..." for k, v in list(return_cookies_dict.items())[:5])
                    )
                except Exception as e:
                    logger(f"Sentinel Browser cookies 提取失败: {e}")

            try:
                parsed = json.loads(token)
                logger(
                    "Sentinel Browser 成功: "
                    f"p={'✓' if parsed.get('p') else '✗'} "
                    f"t={'✓' if parsed.get('t') else '✗'} "
                    f"c={'✓' if parsed.get('c') else '✗'}"
                )
            except Exception:
                logger(f"Sentinel Browser 成功: len={len(token)}")

            return token
        except Exception as e:
            logger(f"Sentinel Browser 异常: {e}")
            return None
        finally:
            browser.close()


def submit_password_via_browser(
    *,
    email: str,
    password: str,
    sentinel_token: str,
    device_id: Optional[str] = None,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    headless: bool = True,
    log_fn: Optional[Callable[[str], None]] = None,
    auth_cookies: Optional[dict] = None,
) -> dict:
    """在真实浏览器中提交注册密码，返回 OpenAI 响应 JSON。
    
    完全绕过 curl_cffi，避免 Cloudflare Session 不匹配问题。
    
    Args:
        auth_cookies: 从 HTTP session 继承的认证 Cookie（如 __Host-authjs.session-token），
                      传入 dict {cookie_name: cookie_value}，会在浏览器上下文中设置。
    
    Returns:
        {"status_code": int, "body": dict|str, "success": bool}
    """
    logger = log_fn or (lambda _msg: None)

    page_url = "https://auth.openai.com/create-account/password"
    register_url = "https://auth.openai.com/api/accounts/user/register"

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        logger(f"Playwright 不可用: {e}")
        return {"status_code": 0, "body": str(e), "success": False}

    launch_args = {
        "headless": bool(headless),
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
            "--disable-features=VizDisplayCompositor",
            "--ignore-certificate-errors",
        ],
    }
    proxy_config = build_playwright_proxy_config(proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config

    logger(f"Browser 提交密码: url={page_url}, email={email}, headless={headless}")

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.7103.92 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            if device_id:
                try:
                    context.add_cookies([{
                        "name": "oai-did",
                        "value": str(device_id),
                        "url": "https://auth.openai.com/",
                        "path": "/",
                        "secure": True,
                        "sameSite": "Lax",
                    }])
                except Exception:
                    pass

            # 从 HTTP session 同步认证 Cookie 到浏览器（关键：保持 session 连续性）
            if auth_cookies:
                for cookie_name, cookie_value in auth_cookies.items():
                    if cookie_value:
                        try:
                            context.add_cookies([{
                                "name": cookie_name,
                                "value": str(cookie_value),
                                "url": "https://auth.openai.com/",
                                "domain": ".auth.openai.com",
                                "path": "/",
                                "secure": True,
                                "sameSite": "Lax",
                            }])
                        except Exception:
                            pass
                logger(f"Browser 密码提交: 已同步 {len(auth_cookies)} 个认证 Cookie")

            page = context.new_page()

            # 1. 导航到密码页面，通过 Cloudflare
            logger("Browser 密码提交: 导航到密码页面...")
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)

            # 2. 等待并确认 Cloudflare 已通过（最多等 60 秒）
            cf_passed = False
            for _ in range(30):
                try:
                    page_url_current = page.url
                    page_title = page.title()
                    page_content = page.content()[:300]
                except Exception:
                    page.wait_for_timeout(2000)
                    continue
                
                is_cf = (
                    "challenges.cloudflare.com" in page_content
                    or "Just a moment" in page_title
                )
                
                if not is_cf:
                    cf_passed = True
                    logger(f"Browser 密码提交: Cloudflare 已通过, URL={page_url_current[:80]}")
                    break
                
                logger(f"Browser 密码提交: 等待 Cloudflare... ({_+1}/30)")
                page.wait_for_timeout(2000)
            
            if not cf_passed:
                logger("Browser 密码提交: Cloudflare 超时未通过，可能 IP 被限制")
                return {
                    "status_code": 0,
                    "body": "Cloudflare challenge timeout - IP may be blocked",
                    "success": False,
                }

            # 再多等一会让 JS 初始化
            page.wait_for_timeout(2000)

            # 3. 提交密码表单
            # 关键：不用 page.evaluate() 里的 fetch()，因为 Cloudflare 的 service worker 会拦截 JS 层的 fetch。
            # 改用 Playwright 的 context.request，它走浏览器原生网络栈，和页面共享 Cookie，Cloudflare 不拦截。
            body = {
                "password": password,
                "username": email,
            }
            logger(f"Browser 密码提交: 发送注册请求 {json.dumps(body)[:200]}")

            for attempt in range(3):
                if attempt > 0:
                    logger(f"Browser 密码提交: 重试第 {attempt} 次...")
                    page.wait_for_timeout(3000)

                try:
                    api_resp = context.request.post(
                        register_url,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "openai-sentinel-token": sentinel_token,
                            "Origin": "https://auth.openai.com",
                            "Referer": "https://auth.openai.com/create-account/password",
                        },
                        data=json.dumps(body),
                    )
                    status = api_resp.status
                    try:
                        body_data = api_resp.json()
                    except Exception:
                        body_data = api_resp.text()
                except Exception as fetch_err:
                    status = 0
                    body_data = str(fetch_err)

                # 检查是否是 Cloudflare 拦截
                body_str = str(body_data)[:300] if not isinstance(body_data, dict) else ""
                is_cf_block = (
                    "Just a moment" in body_str
                    or "challenges.cloudflare.com" in body_str
                    or "cf-challenge" in body_str
                )

                if not is_cf_block:
                    logger(f"Browser 密码提交: 状态={status}")
                    return {
                        "status_code": status,
                        "body": body_data,
                        "success": 200 <= status < 400,
                    }

                logger(f"Browser 密码提交: 请求被 Cloudflare 拦截，等待后重试...")

            # 所有重试都失败了
            logger("Browser 密码提交: 所有重试均被 Cloudflare 拦截")
            return {
                "status_code": 0,
                "body": "Cloudflare block after retries",
                "success": False,
            }

        except Exception as e:
            logger(f"Browser 密码提交异常: {e}")
            return {"status_code": 0, "body": str(e), "success": False}
        finally:
            browser.close()
