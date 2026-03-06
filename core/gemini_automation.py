"""
Gemini自动化登录模块（用于新账号注册）
"""
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from DrissionPage import ChromiumPage, ChromiumOptions
from core.base_task_service import TaskCancelledError


# 常量
LOGIN_URL = (
    "https://auth.business.gemini.google/login"
    "?continueUrl=https:%2F%2Fbusiness.gemini.google%2F"
    "&wiffid=CAoSJDIwNTlhYzBjLTVlMmMtNGUxZS1hY2JkLThmOGY2ZDE0ODM1Mg"
)

# XPath 定位（与 Gemini-Business 注册脚本一致）
XPATH = {
    "email_input": "/html/body/c-wiz/div/div/div[1]/div/div/div/form/div[1]/div[1]/div/span[2]/input",
    "continue_btn": "/html/body/c-wiz/div/div/div[1]/div/div/div/form/div[2]/div/button",
    "verify_btn": "/html/body/c-wiz/div/div/div[1]/div/div/div/form/div[2]/div/div[1]/span/div[1]/button",
}

# Linux 下常见的 Chromium 路径
CHROMIUM_PATHS = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/lib/chromium/chromium",
    "/opt/google/chrome/google-chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
]

# 注册时随机使用的真实英文姓名（避免明显的机器人特征）
REGISTER_NAMES = [
    "James Smith", "John Johnson", "Robert Williams", "Michael Brown", "William Jones",
    "David Garcia", "Mary Miller", "Patricia Davis", "Jennifer Rodriguez", "Linda Martinez",
    "Barbara Anderson", "Susan Thomas", "Jessica Jackson", "Sarah White", "Karen Harris",
    "Lisa Martin", "Nancy Thompson", "Betty Garcia", "Margaret Martinez", "Sandra Robinson",
    "Ashley Clark", "Dorothy Rodriguez", "Emma Lewis", "Olivia Lee", "Ava Walker",
    "Emily Hall", "Abigail Allen", "Madison Young", "Elizabeth Hernandez", "Charlotte King",
]


def _find_chromium_path() -> Optional[str]:
    """查找可用的 Chromium/Chrome 浏览器路径"""
    for path in CHROMIUM_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _has_graphical_session() -> bool:
    """当前环境是否具备可用的图形会话。"""
    return bool((os.environ.get("DISPLAY") or "").strip() or (os.environ.get("WAYLAND_DISPLAY") or "").strip())


class GeminiAutomation:
    """Gemini自动化登录"""

    def __init__(
        self,
        user_agent: str = "",
        proxy: str = "",
        headless: bool = True,
        timeout: int = 60,
        log_callback=None,
    ) -> None:
        self.user_agent = user_agent or self._get_ua()
        self.proxy = proxy
        self.headless = headless
        self.timeout = timeout
        self.log_callback = log_callback
        self._page = None
        self._user_data_dir = None

    def stop(self) -> None:
        """外部请求停止：尽力关闭浏览器实例。"""
        page = self._page
        if page:
            try:
                page.quit()
            except Exception:
                pass

    def login_and_extract(self, email: str, mail_client, is_new_account: bool = False) -> dict:
        """执行登录并提取配置"""
        page = None
        user_data_dir = None
        try:
            page = self._create_page()
            user_data_dir = getattr(page, 'user_data_dir', None)
            self._page = page
            self._user_data_dir = user_data_dir
            return self._run_flow(page, email, mail_client, is_new_account=is_new_account)
        except TaskCancelledError:
            raise
        except Exception as exc:
            self._log("error", f"automation error: {exc}")
            return {"success": False, "error": str(exc)}
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
            self._page = None
            self._cleanup_user_data(user_data_dir)
            self._user_data_dir = None

    def _create_page(self) -> ChromiumPage:
        """创建浏览器页面，并在 Linux/无显示环境下做稳妥回退。"""
        effective_headless = self.headless
        if not effective_headless and not _has_graphical_session():
            effective_headless = True
            self._log("warning", "未检测到图形会话，自动切换为无头模式启动浏览器")

        try:
            return self._launch_page(effective_headless)
        except Exception as exc:
            if self.headless or effective_headless:
                raise
            self._log("warning", f"有头模式启动失败，自动切换无头模式重试: {exc}")
            return self._launch_page(True)

    def _launch_page(self, headless: bool) -> ChromiumPage:
        """按指定模式启动浏览器。"""
        options = self._build_browser_options(headless)
        page = ChromiumPage(options)
        page.set.timeouts(self.timeout)

        return page

    def _build_browser_options(self, headless: bool) -> ChromiumOptions:
        """构建浏览器启动参数。"""
        options = ChromiumOptions()

        # 自动检测 Chromium 浏览器路径（Linux/Docker 环境）
        chromium_path = _find_chromium_path()
        if chromium_path:
            options.set_browser_path(chromium_path)

        # 仅保留 Docker/Linux 环境必需的参数（参考项目零参数启动）
        options.set_argument("--no-sandbox")
        options.set_argument("--disable-dev-shm-usage")
        options.set_argument("--disable-setuid-sandbox")
        options.set_argument("--no-first-run")
        options.set_argument("--no-default-browser-check")
        options.set_argument("--window-size=1920,1080")

        if self.proxy:
            options.set_argument(f"--proxy-server={self.proxy}")

        if headless:
            options.set_argument("--headless=new")
            options.set_argument("--disable-gpu")

        options.auto_port()
        return options

    def _fast_type(self, element, text: str, delay: float = 0.02) -> None:
        """快速逐字符输入文本（与 Gemini-Business 脚本一致）"""
        for c in text:
            element.input(c)
            time.sleep(delay)

    def _run_flow(self, page, email: str, mail_client, is_new_account: bool = False) -> dict:
        """执行登录流程（与 Gemini-Business 注册脚本一致的 XPath + 直接输入方式）"""

        # 记录任务开始时间，用于邮件时间过滤
        from datetime import datetime
        task_start_time = datetime.now()

        # Step 1: 导航到登录页面（使用带 wiffid 参数的 URL）
        self._log("info", f"打开登录页面: {email}")
        page.get(LOGIN_URL, timeout=self.timeout)
        time.sleep(random.uniform(2, 4))

        # Step 2: 检查是否已登录（直接进入工作台）
        current_url = page.url
        self._log("info", f"当前 URL: {current_url}")

        if "signin-error" in current_url:
            self._log("error", "进入 signin-error 页面，可能是代理或网络问题")
            self._save_screenshot(page, "signin_error")
            return {"success": False, "error": "signin-error: token rejected by Google, try changing proxy"}

        has_business_params = "business.gemini.google" in current_url and "csesidx=" in current_url and "/cid/" in current_url
        if has_business_params:
            self._log("info", "已登录，提取配置")
            return self._extract_config(page, email)

        # 检测 403 Access Restricted
        access_error = self._check_access_restricted(page, email)
        if access_error:
            return access_error

        # Step 3: 输入邮箱（XPath 定位输入框）
        self._log("info", f"输入邮箱: {email}")
        email_input = page.ele(f"xpath:{XPATH['email_input']}", timeout=15)
        if not email_input:
            # 降级尝试 CSS 选择器
            email_input = page.ele("css:input[type='email']", timeout=5) or \
                          page.ele("css:input[name='email']", timeout=3)
        if not email_input:
            self._log("error", "未找到邮箱输入框")
            self._save_screenshot(page, "email_input_missing")
            return {"success": False, "error": "email input not found"}

        email_input.click()
        time.sleep(random.uniform(0.1, 0.3))
        email_input.clear()
        self._fast_type(email_input, email)

        # Step 4: 点击继续按钮
        time.sleep(0.5)
        continue_btn = page.ele(f"xpath:{XPATH['continue_btn']}", timeout=10)
        if not continue_btn:
            # 降级尝试 CSS 选择器
            try:
                for btn in page.eles("tag:button"):
                    text = (btn.text or "").strip().lower()
                    if text and any(kw in text for kw in ["继续", "continue", "next", "下一步"]):
                        continue_btn = btn
                        break
            except Exception:
                pass
        if not continue_btn:
            self._log("error", "未找到继续按钮")
            self._save_screenshot(page, "continue_btn_missing")
            return {"success": False, "error": "continue button not found"}

        try:
            page.run_js("arguments[0].click();", continue_btn)
        except Exception:
            continue_btn.click()
        self._log("info", "点击继续")

        # Step 5: 等待页面跳转
        time.sleep(3)
        current_url = page.url
        self._log("info", f"点击继续后页面: {current_url}")

        # 检测 signin-error（邮箱被拒绝、风控触发等）
        if "signin-error" in current_url:
            self._log("error", "点击继续后进入 signin-error 页面，可能是代理或域名问题")
            self._save_screenshot(page, "signin_error_after_continue")
            return {"success": False, "error": "signin-error after continue: email rejected, try changing proxy or domain"}

        # 检查是否直接进入了工作台（无需验证码，刷新场景可能出现）
        if "business.gemini.google" in current_url and "/cid/" in current_url:
            self._log("info", "直接进入工作台，无需验证码")
            return self._extract_config(page, email)

        # 检测 403 Access Restricted
        access_error = self._check_access_restricted(page, email)
        if access_error:
            return access_error

        # Step 6: 等待邮箱验证码（同时监控浏览器 URL，检测 signin-error 提前终止）
        self._log("info", "等待邮箱验证码...")
        code = None
        poll_start = time.time()
        poll_timeout = 90
        while time.time() - poll_start < poll_timeout:
            elapsed = int(time.time() - poll_start)

            # 监控浏览器 URL：如果中途跳转到 signin-error 则立即终止
            try:
                browser_url = page.url
                if "signin-error" in browser_url:
                    self._log("error", f"等待验证码期间浏览器跳转到 signin-error ({elapsed}s)")
                    self._save_screenshot(page, "signin_error_during_poll")
                    return {"success": False, "error": "signin-error during code wait: Google rejected the request"}
            except Exception:
                pass

            # 单次轮询验证码（短超时，不长时间阻塞）
            code = mail_client.poll_for_code(timeout=3, interval=3, since_time=task_start_time)
            if code:
                break

        if not code:
            self._log("error", "验证码超时")
            self._save_screenshot(page, "code_timeout")
            return {"success": False, "error": "verification code timeout"}

        self._log("info", f"收到验证码: {code}")

        # Step 7: 输入验证码
        time.sleep(1)
        self._log("info", f"输入验证码: {code}")

        # 尝试多种选择器查找验证码输入框
        code_input = page.ele("css:input[name='pinInput']", timeout=5)
        if not code_input:
            code_input = page.ele("css:input[jsname='ovqh0b']", timeout=3) or \
                         page.ele("css:input[type='tel']", timeout=2) or \
                         page.ele("css:input[autocomplete='one-time-code']", timeout=2)

        if code_input:
            code_input.click()
            time.sleep(0.1)
            self._fast_type(code_input, code, delay=0.05)
        else:
            # 降级：尝试 span[data-index='0'] 方式
            try:
                span = page.ele("css:span[data-index='0']", timeout=3)
                if span:
                    span.click()
                    time.sleep(0.2)
                    page.actions.type(code)
                else:
                    self._log("error", "验证码输入框未找到")
                    self._save_screenshot(page, "code_input_missing")
                    return {"success": False, "error": "code input not found"}
            except Exception as e:
                self._log("error", f"验证码输入失败: {e}")
                return {"success": False, "error": f"code input failed: {e}"}

        # Step 8: 点击验证按钮
        time.sleep(0.5)
        verify_clicked = False
        try:
            vbtn = page.ele(f"xpath:{XPATH['verify_btn']}", timeout=5)
            if vbtn:
                try:
                    page.run_js("arguments[0].click();", vbtn)
                    verify_clicked = True
                except Exception:
                    vbtn.click()
                    verify_clicked = True
        except Exception:
            pass

        if not verify_clicked:
            # 降级：搜索所有按钮找验证按钮
            try:
                for btn in page.eles("tag:button"):
                    text = (btn.text or "").strip()
                    if text and any(kw in text for kw in ["验证", "Verify", "verify", "确认"]):
                        try:
                            page.run_js("arguments[0].click();", btn)
                            verify_clicked = True
                        except Exception:
                            btn.click()
                            verify_clicked = True
                        break
            except Exception:
                pass

        if verify_clicked:
            self._log("info", "点击验证")
        else:
            # 最后兜底：回车提交
            self._log("info", "未找到验证按钮，尝试回车提交")
            if code_input:
                code_input.input("\n")

        # Step 9: 注册场景 - 处理姓名输入
        if is_new_account:
            time.sleep(3)
            access_error = self._check_access_restricted(page, email)
            if access_error:
                return access_error
            self._log("info", "[注册] 验证码已提交，等待姓名输入页面...")
            if self._handle_username_setup(page, is_new_account=True):
                self._log("info", "姓名填写完成，等待工作台 URL...")
                if self._wait_for_business_params(page, timeout=45):
                    self._log("info", "注册成功，提取配置...")
                    return self._extract_config(page, email)
            # 姓名步骤失败或未出现，继续走通用流程兜底
            self._log("info", "姓名步骤未完成，走通用流程兜底...")

        # Step 10: 等待页面自动重定向
        self._log("info", "等待验证后跳转...")
        for _ in range(30):
            time.sleep(1)
            try:
                url = page.url
            except Exception:
                continue
            if "business.gemini.google" in url and "/cid/" in url:
                self._log("info", f"已进入工作台: {url}")
                break
        else:
            try:
                self._log("warning", f"未跳转到工作台，当前: {page.url}")
            except Exception:
                self._log("warning", "未跳转到工作台，且浏览器状态异常")

        # 检查验证码提交状态
        current_url = page.url
        self._log("info", f"验证后 URL: {current_url}")

        if "verify-oob-code" in current_url:
            self._log("error", "验证码提交失败")
            self._save_screenshot(page, "verification_submit_failed")
            return {"success": False, "error": "verification code submission failed"}

        # Step 11: 处理协议页面（如果有）
        self._handle_agreement_page(page)

        # 检测 403 Access Restricted 页面
        access_error = self._check_access_restricted(page, email)
        if access_error:
            return access_error

        # Step 12: 检查是否已经在正确的页面
        current_url = page.url
        has_business_params = "business.gemini.google" in current_url and "csesidx=" in current_url and "/cid/" in current_url
        if has_business_params:
            return self._extract_config(page, email)

        # Step 13: 如果不在正确的页面，尝试导航
        if "business.gemini.google" not in current_url:
            page.get("https://business.gemini.google/", timeout=self.timeout)
            time.sleep(random.uniform(4, 7))

        # 检查是否需要设置用户名（仅登录刷新走此路径，注册已在早期处理）
        if not is_new_account and "cid" not in page.url:
            if self._handle_username_setup(page):
                time.sleep(random.uniform(4, 7))

        # 再次检测 403
        access_error = self._check_access_restricted(page, email)
        if access_error:
            return access_error

        # 等待 URL 参数生成（csesidx 和 cid）
        if not self._wait_for_business_params(page):
            page.refresh()
            time.sleep(random.uniform(4, 7))
            if not self._wait_for_business_params(page):
                self._log("error", "URL 参数生成失败")
                self._save_screenshot(page, "params_missing")
                return {"success": False, "error": "URL parameters not found"}

        # 提取配置
        self._log("info", "登录成功，提取配置...")
        return self._extract_config(page, email)

    def _check_access_restricted(self, page, email: str = "") -> dict | None:
        """检测 403 Access Restricted 页面，返回错误 dict 或 None"""
        domain = email.split("@")[1] if "@" in email else "unknown"
        error_msg = f"403 域名封禁 ({domain})"

        # 方法1: 搜索 h1 标签
        try:
            h1 = page.ele("tag:h1", timeout=2)
            h1_text = h1.text if h1 else ""
            if h1_text and "Access Restricted" in h1_text:
                self._log("error", "⛔ 403 Access Restricted: email banned by Google")
                self._log("error", f"⛔ 403 访问受限，域名 {domain} 可能已被 Google 封禁")
                self._save_screenshot(page, "access_restricted_403")
                return {"success": False, "error": error_msg}
        except Exception:
            pass

        # 方法2: body 文本
        try:
            body = page.ele("tag:body", timeout=2)
            body_text = (body.text or "")[:500] if body else ""
            if "Access Restricted" in body_text:
                self._log("error", "⛔ 403 Access Restricted: email banned by Google")
                self._log("error", f"⛔ 403 访问受限，域名 {domain} 可能已被 Google 封禁")
                self._save_screenshot(page, "access_restricted_403")
                return {"success": False, "error": error_msg}
        except Exception:
            pass

        # 方法3: page.html 源码
        try:
            html = (page.html or "")[:2000]
            if "Access Restricted" in html:
                self._log("error", "⛔ 403 Access Restricted: email banned by Google")
                self._log("error", f"⛔ 403 访问受限，域名 {domain} 可能已被 Google 封禁")
                self._save_screenshot(page, "access_restricted_403")
                return {"success": False, "error": error_msg}
        except Exception:
            pass

        return None

    def _handle_agreement_page(self, page) -> None:
        """处理协议页面"""
        if "/admin/create" in page.url:
            agree_btn = page.ele("css:button.agree-button", timeout=5)
            if agree_btn:
                agree_btn.click()
                time.sleep(random.uniform(2, 4))

    def _wait_for_cid(self, page, timeout: int = 10) -> bool:
        """等待URL包含cid"""
        for _ in range(timeout):
            if "cid" in page.url:
                return True
            time.sleep(1)
        return False

    def _wait_for_business_params(self, page, timeout: int = 30) -> bool:
        """等待业务页面参数生成（csesidx 和 cid）"""
        for _ in range(timeout):
            url = page.url
            if "csesidx=" in url and "/cid/" in url:
                return True
            time.sleep(1)
        return False

    def _handle_username_setup(self, page, is_new_account: bool = False) -> bool:
        """处理用户名设置页面（is_new_account=True 时启用按钮兜底和延长超时）"""
        current_url = page.url

        if "auth.business.gemini.google/login" in current_url:
            return False

        # 精准选择器（参考实际页面 DOM，优先级从高到低）
        selectors = [
            "css:input[formcontrolname='fullName']",
            "css:input#mat-input-0",
            "css:input[placeholder='全名']",
            "css:input[placeholder='Full name']",
            "css:input[name='displayName']",
            "css:input[aria-label*='用户名' i]",
            "css:input[aria-label*='display name' i]",
            "css:input[type='text']",
        ]

        # 轮询等待输入框出现（最多30秒，每秒检查一次）
        # 与参考代码对齐：页面加载慢时不会过早放弃
        username_input = None
        self._log("info", "⏳ 等待用户名输入框出现（最多30秒）...")
        for i in range(30):
            for selector in selectors:
                try:
                    el = page.ele(selector, timeout=1)
                    if el:
                        username_input = el
                        self._log("info", f"✅ 找到用户名输入框: {selector}")
                        break
                except Exception:
                    continue
            if username_input:
                break
            time.sleep(1)

        if not username_input:
            self._log("warning", "⚠️ 30秒内未找到用户名输入框，跳过此步骤")
            return False

        name = random.choice(REGISTER_NAMES)
        self._log("info", f"输入姓名: {name}")

        try:
            # 清空输入框
            username_input.click()
            time.sleep(0.2)
            username_input.clear()
            time.sleep(0.1)

            # 逐字符输入姓名（与 Gemini-Business 脚本一致）
            self._fast_type(username_input, name)

            # 回车提交
            time.sleep(0.3)
            username_input.input("\n")

            if is_new_account:
                # 注册专用：回车后等待1.5秒，若未跳转则用按钮兜底
                time.sleep(random.uniform(1.5, 3))
                if "cid" not in page.url:
                    self._log("info", "⌨️ 回车未跳转，尝试点击提交按钮...")
                    try:
                        for btn in page.eles("tag:button"):
                            try:
                                if btn.is_displayed() and btn.is_enabled():
                                    btn.click()
                                    self._log("info", "✅ 已点击提交按钮（兜底）")
                                    time.sleep(1)
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        self._log("warning", f"⚠️ 按钮兜底失败: {e}")

                # 注册专用：等待45秒，失败则刷新再等15秒
                if not self._wait_for_cid(page, timeout=45):
                    self._log("warning", "⚠️ 用户名提交后未检测到 cid 参数，尝试刷新...")
                    page.refresh()
                    time.sleep(random.uniform(2, 4))
                    if not self._wait_for_cid(page, timeout=15):
                        self._log("error", "❌ 刷新后仍未检测到 cid 参数")
                        self._save_screenshot(page, "step7_after_verify")
                        return False
            else:
                # 登录刷新：原有30秒逻辑
                if not self._wait_for_cid(page, timeout=30):
                    self._log("warning", "⚠️ 用户名提交后未检测到 cid 参数")
                    return False

            return True
        except Exception as e:
            self._log("warning", f"⚠️ 用户名设置异常: {e}")
            return False

    def _extract_config(self, page, email: str) -> dict:
        """提取配置（轮询等待 cookie 到位）"""
        try:
            if "cid/" not in page.url:
                page.get("https://business.gemini.google/", timeout=self.timeout)
                time.sleep(random.uniform(2, 4))

            url = page.url
            if "cid/" not in url:
                return {"success": False, "error": "cid not found"}

            config_id = url.split("cid/")[1].split("?")[0].split("/")[0]
            csesidx = url.split("csesidx=")[1].split("&")[0] if "csesidx=" in url else ""

            # 轮询等待关键 cookie 到位（最多10秒）
            ses = None
            host = None
            ses_obj = None
            for _ in range(10):
                cookies = page.cookies()
                ses = next((c["value"] for c in cookies if c["name"] == "__Secure-C_SES"), None)
                host = next((c["value"] for c in cookies if c["name"] == "__Host-C_OSES"), None)
                ses_obj = next((c for c in cookies if c["name"] == "__Secure-C_SES"), None)
                if ses and host:
                    break
                time.sleep(1)

            if not ses or not host:
                self._log("warning", f"⚠️ Cookie 不完整 (ses={'有' if ses else '无'}, host={'有' if host else '无'})")

            # 使用北京时区，确保时间计算正确（Cookie expiry 是 UTC 时间戳）
            beijing_tz = timezone(timedelta(hours=8))
            if ses_obj and "expiry" in ses_obj:
                cookie_expire_beijing = datetime.fromtimestamp(ses_obj["expiry"], tz=beijing_tz)
                expires_at = (cookie_expire_beijing - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
            else:
                expires_at = (datetime.now(beijing_tz) + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")

            config = {
                "id": email,
                "csesidx": csesidx,
                "config_id": config_id,
                "secure_c_ses": ses,
                "host_c_oses": host,
                "expires_at": expires_at,
            }

            # 提取试用期信息
            trial_end = self._extract_trial_end(page, csesidx, config_id)
            if trial_end:
                config["trial_end"] = trial_end

            return {"success": True, "config": config}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_trial_end(self, page, csesidx: str, config_id: str) -> Optional[str]:
        """从页面中提取试用期到期日期，不跳转到可能 400 的深层路径"""
        # re 已在文件顶部导入
        try:
            self._log("info", "📅 获取试用期信息...")

            def _days_to_end_date(days: int) -> str:
                end_date = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=days)).strftime("%Y-%m-%d")
                self._log("info", f"📅 试用期剩余 {days} 天，到期日: {end_date}")
                return end_date

            def _search_page_source(source: str) -> Optional[str]:
                """在页面源码中搜索试用期信息"""
                # 格式1: "daysLeft":29 (JSON数据)
                m = re.search(r'"daysLeft"\s*:\s*(\d+)', source)
                if m:
                    return _days_to_end_date(int(m.group(1)))
                # 格式2: "trialDaysRemaining":29
                m = re.search(r'"trialDaysRemaining"\s*:\s*(\d+)', source)
                if m:
                    return _days_to_end_date(int(m.group(1)))
                # 格式3: 日期数组 "[2026,3,25]" 形式 (batchexecute格式)
                m = re.search(r'\[(\d{4}),(\d{1,2}),(\d{1,2})\].*?\[(\d{4}),(\d{1,2}),(\d{1,2})\]', source)
                if m:
                    # 取第二个日期（结束日期）
                    try:
                        end_date = f"{m.group(4):0>4}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
                        # 简单校验年份合理
                        if 2025 <= int(m.group(4)) <= 2030:
                            self._log("info", f"📅 试用期到期日: {end_date}")
                            return end_date
                    except Exception:
                        pass
                # 格式4: "29 days left" 或 "还剩29天"
                m = re.search(r'(\d+)\s*days?\s*left', source, re.IGNORECASE)
                if m:
                    return _days_to_end_date(int(m.group(1)))
                m = re.search(r'还剩\s*(\d+)\s*天', source)
                if m:
                    return _days_to_end_date(int(m.group(1)))
                return None

            # ——— 方式1: 当前页面（刚登录完，不需要跳转）———
            try:
                source = page.html
                result = _search_page_source(source or "")
                if result:
                    return result
            except Exception:
                pass

            # ——— 方式2: 跳转到 /settings（不带 billing/plans 后缀，SPA可以处理）———
            try:
                settings_url = f"https://business.gemini.google/cid/{config_id}/settings?csesidx={csesidx}"
                page.get(settings_url, timeout=self.timeout)
                time.sleep(random.uniform(1.5, 3))
                source = page.html
                result = _search_page_source(source or "")
                if result:
                    return result
            except Exception:
                pass

            # ——— 方式3: 跳转到主页（最保险）———
            try:
                main_url = f"https://business.gemini.google/cid/{config_id}?csesidx={csesidx}"
                page.get(main_url, timeout=self.timeout)
                time.sleep(random.uniform(1.5, 3))
                source = page.html
                result = _search_page_source(source or "")
                if result:
                    return result
            except Exception:
                pass

            self._log("warning", "⚠️ 未能获取试用期信息（页面中未找到相关数据）")
            return None
        except Exception as e:
            self._log("warning", f"⚠️ 获取试用期失败: {e}")
            return None

    def _save_screenshot(self, page, name: str) -> None:
        """保存截图"""
        try:
            from core.storage import _data_file_path
            screenshot_dir = _data_file_path("automation")
            os.makedirs(screenshot_dir, exist_ok=True)
            path = os.path.join(screenshot_dir, f"{name}_{int(time.time())}.png")
            page.get_screenshot(path=path)
        except Exception:
            pass

    def _log(self, level: str, message: str) -> None:
        """记录日志"""
        if self.log_callback:
            try:
                self.log_callback(level, message)
            except TaskCancelledError:
                raise
            except Exception:
                pass

    def _cleanup_user_data(self, user_data_dir: Optional[str]) -> None:
        """清理浏览器用户数据目录"""
        if not user_data_dir:
            return
        try:
            import shutil
            if os.path.exists(user_data_dir):
                shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass

    @staticmethod
    def _get_ua() -> str:
        """返回空字符串，让浏览器使用原生 User-Agent（与参考项目一致）。
        伪造 UA 版本与实际浏览器不匹配会被 Google 检测到。"""
        return ""
