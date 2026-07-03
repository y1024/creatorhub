"""快手发布入口(浏览器自动化快手创作者服务平台 cp.kuaishou.com)。

快手 PC 端没有像小红书那样成熟的 execjs 签名直发方案,这里走浏览器自动化:
用账号专属持久 profile(已含创作者登录态)打开发布页,上传文件、填文案、点发布。

⚠️ 实验性:发布页选择器随快手改版可能失效,集中在下面的 _* 选择器常量;
   发布时弹真实窗口,遇验证码/需补封面可在窗口里手动处理。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

VIDEO_URL = "https://cp.kuaishou.com/article/publish/video"
IMAGE_URL = "https://cp.kuaishou.com/article/publish/atlas"
_DESC_SEL = ['div[contenteditable="true"]', '#work-description-edit',
             'textarea[placeholder*="描述"]', '.editor-content', 'textarea']
_PUBLISH_BTN = ['button:has-text("发布")', 'div:has-text("发布作品")',
                '.publish-btn', 'button._button_publish']


async def _click_first(page, selectors, timeout=2500) -> bool:
    for sel in selectors:
        try:
            await page.click(sel, timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _fill_first(page, selectors, text, timeout=2500) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.click(timeout=timeout)
            await el.fill(text, timeout=timeout)
            return True
        except Exception:
            try:
                await page.keyboard.type(text)
                return True
            except Exception:
                continue
    return False


async def publish_kuaishou(mgr: BrowserManager, identity: Identity,
                           storage_state_json: str, media_type: str, title: str,
                           desc: str, media_paths: List[str], topics: str = "",
                           headed: bool = True, timeout_seconds: int = 180
                           ) -> Tuple[bool, str, str]:
    """发布一条快手作品。返回 (ok, result_url, error)。
    storage_state_json 仅用于校验(实际登录态在该账号持久 profile 里)。"""
    files = [str(Path(p)) for p in media_paths if p and Path(p).exists()]
    if not files:
        return False, "", "没有可用的本地媒体文件(路径不存在)"
    tags = [t.strip().lstrip("#") for t in (topics or "").split(",") if t.strip()]
    # 快手正文:标题 + 描述 + 话题
    body = ((title + "\n" if title else "") + (desc or "")
            + ("\n" + " ".join(f"#{t}" for t in tags) if tags else "")).strip()[:1000]

    ctx = await mgr.open_headed(identity)
    page = await ctx.new_page()
    ok, result_url, error = False, "", ""
    try:
        url = VIDEO_URL if media_type == "video" else IMAGE_URL
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(2500)
        if "passport" in page.url or "/login" in page.url:
            return False, "", "logged_out:快手创作平台未登录"
        try:
            await page.locator('input[type="file"]').first.set_input_files(
                files if media_type == "images" else files[:1], timeout=15000)
        except Exception as e:
            return False, "", f"上传文件失败: {e!r}"
        await page.wait_for_timeout(6000 if media_type == "video" else 3500)
        if body:
            await _fill_first(page, _DESC_SEL, body)
        await page.wait_for_timeout(800)
        if not await _click_first(page, _PUBLISH_BTN, timeout=4000):
            return False, "", "未找到发布按钮(发布页可能改版)"
        try:
            await page.get_by_text("发布成功", exact=False).first.wait_for(timeout=15000)
            ok = True
        except Exception:
            ok = False
        result_url = page.url if ok else ""
        if not ok:
            error = "已点发布但未确认成功(请到快手创作平台确认)"
    except Exception as e:
        error = f"发布异常: {e!r}"
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
    return ok, result_url, error
