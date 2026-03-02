import re
from typing import Optional


def extract_verification_code(content: str) -> Optional[str]:
    """从邮件内容提取验证码（与参考项目 Gemini-Business 对齐）

    支持字母数字混合码（如 6ZVMWS）和纯数字码。
    """
    if not content:
        return None
    if not isinstance(content, str):
        return None

    # 优先匹配有上下文的验证码（避免误匹配）
    context_patterns = [
        r"验证码为[：:]\\s*([A-Za-z0-9]{4,8})",
        r"一次性验证码为[：:]\\s*([A-Za-z0-9]{4,8})",
        r"Verification code:?\s*([A-Za-z0-9]{4,8})",
        r"verification code:?\s*([A-Za-z0-9]{4,8})",
        r"code is:?\s*([A-Za-z0-9]{4,8})",
        r"验证码[：:]?\s*([A-Za-z0-9]{4,8})",
        r"验证代码[：:]?\s*([A-Za-z0-9]{4,8})",
        r'class="verification-code"[^>]*>\s*([A-Za-z0-9]{4,8})\s*<',
    ]
    for pattern in context_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            return matches[0]

    # 如果是 HTML，去除标签后再试
    clean = content
    if "<" in content and ">" in content:
        clean = re.sub(r'<[^>]+>', ' ', content)
        clean = re.sub(r'&[a-zA-Z]+;', ' ', clean)
        clean = re.sub(r'&#?\w+;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        for pattern in context_patterns:
            matches = re.findall(pattern, clean, re.IGNORECASE)
            if matches:
                return matches[0]

    # 兜底：独立的 6 位字母数字混合码（至少含一个字母和一个数字）
    standalone = re.findall(r'\b([A-Za-z0-9]{6})\b', clean)
    for candidate in standalone:
        if re.search(r'[A-Za-z]', candidate) and re.search(r'\d', candidate):
            return candidate

    # 最后兜底：独立的 6 位纯数字（排除 CSS 颜色值和 HTML 实体）
    digits = re.findall(r'(?<![#&\w])\b(\d{6})\b', clean)
    if digits:
        return digits[0]

    return None
