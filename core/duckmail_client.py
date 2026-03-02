import os
import random
import string
import time
from typing import Optional

import requests

from core.mail_utils import extract_verification_code
from core.proxy_utils import request_with_proxy_fallback


class DuckMailClient:
    """DuckMail客户端"""

    def __init__(
        self,
        base_url: str = "https://api.duckmail.sbs",
        proxy: str = "",
        verify_ssl: bool = True,
        api_key: str = "",
        log_callback=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.api_key = api_key.strip()
        self.log_callback = log_callback

        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.account_id: Optional[str] = None
        self.token: Optional[str] = None

    def set_credentials(self, email: str, password: str) -> None:
        self.email = email
        self.password = password

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """发送请求并打印详细日志"""
        headers = kwargs.pop("headers", None) or {}
        if self.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.api_key}"
        kwargs["headers"] = headers
        self._log("info", f"📤 发送 {method} 请求: {url}")
        if "json" in kwargs:
            self._log("info", f"📦 请求体: {kwargs['json']}")

        try:
            res = request_with_proxy_fallback(
                requests.request,
                method,
                url,
                proxies=self.proxies,
                verify=self.verify_ssl,
                timeout=kwargs.pop("timeout", 15),
                **kwargs,
            )
            self._log("info", f"📥 收到响应: HTTP {res.status_code}")
            log_body = os.getenv("DUCKMAIL_LOG_BODY", "").strip().lower() in ("1", "true", "yes", "y", "on")
            if res.content and (log_body or res.status_code >= 400):
                try:
                    self._log("info", f"📄 响应内容: {res.text[:500]}")
                except Exception:
                    pass
            return res
        except Exception as e:
            self._log("error", f"❌ 网络请求失败: {e}")
            raise

    def register_account(self, domain: Optional[str] = None) -> bool:
        """注册新邮箱账号"""
        # 获取域名
        if not domain:
            self._log("info", "🔍 正在获取可用域名...")
            domain = self._get_domain()
        self._log("info", f"📧 使用域名: {domain}")

        # 生成随机邮箱和密码
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        timestamp = str(int(time.time()))[-4:]
        self.email = f"t{timestamp}{rand}@{domain}"
        self.password = f"Pwd{rand}{timestamp}"
        self._log("info", f"🎲 生成邮箱: {self.email}")
        self._log("info", f"🔑 生成密码: {self.password}")

        try:
            self._log("info", "📤 正在向 DuckMail 发送注册请求...")
            res = self._request(
                "POST",
                f"{self.base_url}/accounts",
                json={"address": self.email, "password": self.password},
            )
            if res.status_code in (200, 201):
                data = res.json() if res.content else {}
                self.account_id = data.get("id")
                self._log("info", f"✅ DuckMail 注册成功，账户ID: {self.account_id}")
                return True
            else:
                self._log("error", f"❌ DuckMail 注册失败: HTTP {res.status_code}")
        except Exception as e:
            self._log("error", f"❌ DuckMail 注册异常: {e}")
            return False

        self._log("error", "❌ DuckMail 注册失败")
        return False

    def login(self) -> bool:
        """登录获取token"""
        if not self.email or not self.password:
            self._log("error", "❌ 邮箱或密码未设置")
            return False

        try:
            self._log("info", f"🔐 正在登录 DuckMail: {self.email}")
            res = self._request(
                "POST",
                f"{self.base_url}/token",
                json={"address": self.email, "password": self.password},
            )
            if res.status_code == 200:
                data = res.json() if res.content else {}
                token = data.get("token")
                if token:
                    self.token = token
                    self._log("info", f"✅ DuckMail 登录成功，Token: {token[:20]}...")
                    return True
                else:
                    self._log("error", "❌ 响应中未找到 Token")
            else:
                self._log("error", f"❌ DuckMail 登录失败: HTTP {res.status_code}")
        except Exception as e:
            self._log("error", f"❌ DuckMail 登录异常: {e}")
            return False

        self._log("error", "❌ DuckMail 登录失败")
        return False

    def fetch_verification_code(self, since_time=None) -> Optional[str]:
        """获取验证码（与参考项目 Gemini-Business 对齐）"""
        if not self.token:
            self._log("info", "Token 不存在，尝试重新登录...")
            if not self.login():
                self._log("error", "登录失败，无法获取验证码")
                return None

        try:
            # 获取邮件列表（带 Accept header，与参考项目一致）
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            }
            res = self._request(
                "GET",
                f"{self.base_url}/messages",
                headers=headers,
                timeout=8,
            )

            if res.status_code != 200:
                self._log("error", f"获取邮件列表失败: HTTP {res.status_code}")
                return None

            data = res.json() if res.content else {}
            # 兼容多种 API 响应格式
            messages = (
                data.get("hydra:member")
                or data.get("member")
                or data.get("data")
                or []
            )

            if not messages:
                return None

            self._log("info", f"收到 {len(messages)} 封邮件")

            # 与参考项目一致：直接取第一封邮件（最新的）
            msg = messages[0]

            # 调试：打印消息原始数据帮助排查
            self._log("info", f"消息原始字段: {list(msg.keys())}")

            msg_id = msg.get("id") or msg.get("@id")
            if not msg_id:
                self._log("error", f"消息中无 id 字段，原始数据: {str(msg)[:300]}")
                return None

            # Hydra 格式: @id 为 "/messages/123"，提取纯 ID
            if isinstance(msg_id, str) and msg_id.startswith("/messages/"):
                msg_id = msg_id.split("/")[-1]

            self._log("info", f"正在读取邮件详情 (ID: {msg_id})")
            detail = self._request(
                "GET",
                f"{self.base_url}/messages/{msg_id}",
                headers=headers,
                timeout=8,
            )

            if detail.status_code != 200:
                self._log("warning", f"读取邮件详情失败: HTTP {detail.status_code}")
                return None

            payload = detail.json() if detail.content else {}

            # 获取邮件内容
            text_content = payload.get("text") or ""
            html_content = payload.get("html") or ""

            # html/text 字段可能是 list
            if isinstance(html_content, list):
                html_content = html_content[0] if html_content else ""
            if isinstance(text_content, list):
                text_content = text_content[0] if text_content else ""

            # 调试日志：打印邮件内容帮助排查
            self._log("info", f"邮件 text 字段 ({len(text_content)} 字符): {text_content[:500] if text_content else '(空)'}")

            # 与参考项目一致：分别在 text 和 html 中提取验证码
            code = extract_verification_code(text_content) or extract_verification_code(html_content)
            if code:
                self._log("info", f"找到验证码: {code}")
                return code
            else:
                self._log("warning", "邮件中未找到验证码")

            return None

        except Exception as e:
            self._log("error", f"获取验证码异常: {e}")
            return None

    def poll_for_code(
        self,
        timeout: int = 90,
        interval: int = 3,
        since_time=None,
    ) -> Optional[str]:
        """轮询获取验证码（与参考项目 Gemini-Business 对齐）"""
        if not self.token:
            self._log("info", "Token 不存在，尝试登录...")
            if not self.login():
                self._log("error", "登录失败，无法轮询验证码")
                return None

        start = time.time()
        while time.time() - start < timeout:
            elapsed = int(time.time() - start)
            code = self.fetch_verification_code(since_time=since_time)
            if code:
                self._log("info", f"验证码获取成功: {code}")
                return code
            if elapsed % 15 == 0 and elapsed > 0:
                self._log("info", f"[{elapsed}s] 仍在等待验证码...")
            time.sleep(interval)

        self._log("error", f"验证码获取超时 ({timeout}秒)")
        return None

    def _get_domain(self) -> str:
        """获取可用域名（优先使用 duckmail.sbs，与参考项目一致）"""
        try:
            res = self._request("GET", f"{self.base_url}/domains")
            if res.status_code == 200:
                data = res.json() if res.content else {}
                members = data.get("hydra:member", [])
                if members:
                    domains = [m.get("domain") for m in members if m.get("domain")]
                    # 优先选择 duckmail.sbs（参考项目验证可用）
                    for preferred in ("duckmail.sbs",):
                        if preferred in domains:
                            return preferred
                    return domains[0] if domains else "duckmail.sbs"
        except Exception:
            pass
        return "duckmail.sbs"

    def _log(self, level: str, message: str) -> None:
        if self.log_callback:
            try:
                self.log_callback(level, message)
            except Exception:
                pass
