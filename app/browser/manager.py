"""Playwright 浏览器管理器(多账号隔离版)。

每个账号一套**独立持久化 context**(launch_persistent_context):
  - 独立 user-data-dir(cookie/localStorage 天然隔离)
  - 独立代理 / UA / 视口 / 时区 / 指纹
常驻这些 context 并按 LRU 控制同时存活数量(省内存)。
登录/发布用同一 profile 的**有头** context(headless=False)。
对应原项目用 chromedp 的角色。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, async_playwright

from .identity import Identity, fingerprint_script

_STEALTH = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-infobars",
]

# storage_state 里允许注入的 Cookie 字段(playwright add_cookies 接受的键)
_COOKIE_KEYS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def _parse_proxy(s: str) -> Optional[Dict[str, str]]:
    """把 http://user:pass@host:port / socks5://host:port 解析成 Playwright proxy 配置。"""
    s = (s or "").strip()
    if not s:
        return None
    u = urlparse(s if "://" in s else "http://" + s)
    if not u.hostname:
        return None
    scheme = u.scheme or "http"
    port = f":{u.port}" if u.port else ""
    pr: Dict[str, str] = {"server": f"{scheme}://{u.hostname}{port}"}
    if u.username:
        pr["username"] = u.username
    if u.password:
        pr["password"] = u.password
    return pr


def normalize_proxy(s: str) -> str:
    """把用户输入规范成带协议头的代理 URL(httpx 必须带 scheme)。
    裸 host:port -> http://host:port;保留账号密码;无法解析则原样返回。
    例:'1.2.3.4:8080' -> 'http://1.2.3.4:8080'。"""
    s = (s or "").strip()
    if not s or not _parse_proxy(s):
        return s
    u = urlparse(s if "://" in s else "http://" + s)
    scheme = u.scheme or "http"
    auth = ""
    if u.username:
        auth = u.username + (":" + u.password if u.password else "") + "@"
    port = f":{u.port}" if u.port else ""
    return f"{scheme}://{auth}{u.hostname}{port}"


def _sanitize_cookies(cookies: List[dict]) -> List[dict]:
    out = []
    for c in cookies:
        if not c.get("name"):
            continue
        ck = {k: c[k] for k in _COOKIE_KEYS if k in c}
        if ck.get("sameSite") not in ("Strict", "Lax", "None"):
            ck.pop("sameSite", None)
        out.append(ck)
    return out


class BrowserManager:
    def __init__(self, default_ua: str, profiles_root: str = "./data/profiles",
                 max_live: int = 6):
        self.default_ua = default_ua
        self.profiles_root = profiles_root
        self.max_live = max(1, max_live)
        self._pw = None
        self._contexts: Dict[Any, BrowserContext] = {}   # key -> 持久化 context
        self._last_used: Dict[Any, float] = {}
        self._locks: Dict[Any, asyncio.Lock] = {}
        self._cv_lock = asyncio.Lock()                   # 保护 context 字典的创建/驱逐

    async def start(self):
        self._pw = await async_playwright().start()

    async def stop(self):
        for ctx in list(self._contexts.values()):
            try:
                await ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._pw:
            await self._pw.stop()

    # ── 画像 ──
    def identity_for(self, acc) -> Identity:
        return Identity.from_account(acc, self.profiles_root, self.default_ua)

    def anon_identity(self) -> Identity:
        return Identity(account_id=None,
                        profile_dir=str(Path(self.profiles_root) / "_anon"),
                        ua=self.default_ua)

    def lock_for(self, key) -> asyncio.Lock:
        """每账号串行锁:同一账号同一时刻只允许一个浏览器动作。"""
        return self._locks.setdefault(key, asyncio.Lock())

    # ── 持久化 context ──
    async def _launch_persistent(self, identity: Identity, headless: bool = True
                                 ) -> BrowserContext:
        pdir = Path(identity.profile_dir)
        pdir.mkdir(parents=True, exist_ok=True)
        was_empty = not any(pdir.iterdir())
        kwargs: Dict[str, Any] = dict(
            user_data_dir=str(pdir), headless=headless, args=_STEALTH,
            user_agent=identity.ua or self.default_ua,
            viewport={"width": identity.viewport_w, "height": identity.viewport_h},
            locale=identity.locale or "zh-CN",
            timezone_id=identity.timezone_id or "Asia/Shanghai",
        )
        proxy = _parse_proxy(identity.proxy)
        if proxy:
            kwargs["proxy"] = proxy
        ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
        if identity.fp_seed:
            try:
                await ctx.add_init_script(fingerprint_script(identity.fp_seed))
            except Exception:
                pass
        # 迁移桥:全新 profile 首次创建时,把存量登录态 Cookie 注入进去(免重新登录)
        if was_empty and identity.bridge_states:
            cookies: List[dict] = []
            for st in identity.bridge_states:
                try:
                    cookies.extend((json.loads(st or "{}").get("cookies")) or [])
                except Exception:
                    pass
            if cookies:
                try:
                    await ctx.add_cookies(_sanitize_cookies(cookies))
                except Exception:
                    pass
        return ctx

    async def _evict_if_needed(self):
        """常驻 context 超过上限时,关掉最久未用且当前未被锁占用的那个。"""
        while len(self._contexts) >= self.max_live:
            cands = [k for k in self._contexts
                     if not (k in self._locks and self._locks[k].locked())]
            if not cands:
                break
            victim = min(cands, key=lambda k: self._last_used.get(k, 0))
            ctx = self._contexts.pop(victim, None)
            self._last_used.pop(victim, None)
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass

    async def context_for(self, identity: Identity) -> BrowserContext:
        """取(或惰性创建)账号专属常驻 context。"""
        key = identity.key
        async with self._cv_lock:
            ctx = self._contexts.get(key)
            if ctx is None:
                await self._evict_if_needed()
                ctx = await self._launch_persistent(identity, headless=True)
                self._contexts[key] = ctx
            self._last_used[key] = time.time()
            return ctx

    async def new_page(self, identity: Identity, block_media: bool = False):
        """从账号常驻 context 开一个新 page(可屏蔽图片/视频/字体)。用完请 page.close()。"""
        ctx = await self.context_for(identity)
        page = await ctx.new_page()
        if block_media:
            async def _route(route):
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", _route)
        return page

    async def close_context(self, key):
        async with self._cv_lock:
            ctx = self._contexts.pop(key, None)
            self._last_used.pop(key, None)
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass

    async def open_headed(self, identity: Identity) -> BrowserContext:
        """登录/发布:先关掉该账号常驻无头 context(同一 profile 不能并存),
        再开同 profile 的有头 context。调用方用完务必 await ctx.close()(关闭即落盘 Cookie)。"""
        await self.close_context(identity.key)
        return await self._launch_persistent(identity, headless=False)


# 各平台 Cookie 顶域(子域如 creator./edith. 都吃顶域 cookie,一个就够)
_COOKIE_DOMAIN = {
    "douyin": ".douyin.com",
    "xhs": ".xiaohongshu.com",
    "kuaishou": ".kuaishou.com",
}


def cookie_string_to_state(cookie_str: str, platform: str = "douyin") -> str:
    """把粘贴的 Cookie 串转成 Playwright storage_state JSON(兜底登录用)。"""
    domain = _COOKIE_DOMAIN.get(platform, ".douyin.com")
    cookies: List[Dict[str, Any]] = []
    for part in cookie_str.strip().split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if not k:
            continue
        cookies.append({
            "name": k.strip(), "value": v.strip(),
            "domain": domain, "path": "/",
        })
    return json.dumps({"cookies": cookies, "origins": []})
