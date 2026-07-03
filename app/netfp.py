"""统一的 TLS/HTTP2 指纹复刻配置。

所有"裸 HTTP 直连平台接口"的地方(签名直连 API、扫码登录轮询、msToken 申请、
短链跳转、创作平台发布)都应改用 curl_cffi 的 impersonate,让请求的 JA3/JA4 与
HTTP2 指纹复刻真实 Chrome —— 纯 httpx 的 TLS 指纹与浏览器不同,容易被风控按
"非浏览器客户端"识别。

impersonate 目标按调用方传入的 UA 里的 Chrome 大版本自动挑最接近的,UA 升级后无需改这里。

注:浏览器拦截路径(Playwright 真实 Chrome)本身就是真指纹,不需要、也不应使用本模块。
"""
from __future__ import annotations

import re

# curl_cffi 0.15 支持的 Chrome impersonate 大版本(随库升级可补)。
_IMPERSONATE_CHROME = (100, 101, 104, 107, 110, 116, 119, 120, 123, 124,
                       131, 136, 142, 145, 146)

DEFAULT_IMPERSONATE = "chrome131"


def impersonate_for_ua(user_agent: str) -> str:
    """按 UA 里的 Chrome 大版本挑最接近的 impersonate 目标(对齐 TLS 指纹与 UA)。"""
    m = re.search(r"Chrome/(\d+)", user_agent or "")
    if not m:
        return DEFAULT_IMPERSONATE
    major = int(m.group(1))
    best = min(_IMPERSONATE_CHROME, key=lambda v: abs(v - major))
    return f"chrome{best}"
