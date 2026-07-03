"""账号设备/网络画像(Identity)。

多账号防关联的核心:每个账号一套**独立且永久固定**的浏览器画像 ——
持久化 profile 目录、固定 UA / 视口 / 时区、专属代理、确定性指纹种子。
画像在登录/建号时生成一次,之后不再变化(指纹漂移本身也是风控信号)。
"""
from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# 真实机型 User-Agent 池(Windows/Mac Chrome,版本接近主流)。
# 一号选定一条后固定;切勿频繁变更。
UA_POOL: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# 常见桌面分辨率(视口)
VIEWPORTS = [(1280, 800), (1366, 768), (1440, 900), (1536, 864), (1600, 900), (1920, 1080)]

# 国内账号统一东八区,务必与代理 IP 地区一致(别 IP 在国内、时区在美洲)
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_LOCALE = "zh-CN"


@dataclass
class Identity:
    """一个账号的完整浏览器画像。account_id=None 表示匿名(未绑定账号的公开抓取)。"""
    account_id: Optional[int]
    profile_dir: str
    proxy: str = ""
    ua: str = ""
    viewport_w: int = 1280
    viewport_h: int = 800
    timezone_id: str = DEFAULT_TZ
    locale: str = DEFAULT_LOCALE
    fp_seed: str = ""
    # 迁移桥:首次为存量账号创建持久 profile 时,把这些登录态 Cookie 注入进去。
    bridge_states: tuple = ()

    @property
    def key(self):
        return self.account_id if self.account_id is not None else "_anon"

    @classmethod
    def from_account(cls, acc, profiles_root: str, default_ua: str) -> "Identity":
        pdir = acc.profile_dir or str(Path(profiles_root) / f"acc_{acc.id}")
        bridge = tuple(s for s in (getattr(acc, "storage_state", ""),
                                   getattr(acc, "creator_storage_state", "")) if s)
        return cls(
            account_id=acc.id, profile_dir=pdir, proxy=acc.proxy or "",
            ua=acc.ua or default_ua,
            viewport_w=acc.viewport_w or 1280, viewport_h=acc.viewport_h or 800,
            timezone_id=acc.timezone_id or DEFAULT_TZ,
            locale=acc.locale or DEFAULT_LOCALE,
            fp_seed=acc.fp_seed or seed_from_id(acc.id),
            bridge_states=bridge,
        )


def seed_from_id(account_id) -> str:
    """没有显式种子时,用账号 id 派生一个稳定种子(保证同账号每次指纹一致)。"""
    return hashlib.md5(f"creatorhub-acc-{account_id}".encode()).hexdigest()


def generate_identity_fields() -> dict:
    """生成一套全新的画像字段(建号/登录时调用一次,写库后永久固定)。"""
    seed = uuid.uuid4().hex
    rnd = random.Random(seed)
    w, h = rnd.choice(VIEWPORTS)
    return {
        "ua": rnd.choice(UA_POOL),
        "viewport_w": w, "viewport_h": h,
        "timezone_id": DEFAULT_TZ, "locale": DEFAULT_LOCALE,
        "fp_seed": seed,
    }


def fingerprint_script(seed: str) -> str:
    """基于 seed 确定性派生的指纹注入脚本(add_init_script)。
    覆盖 navigator.webdriver / hardwareConcurrency / deviceMemory,
    并给 canvas.toDataURL / getImageData 加固定微噪声(同账号每次一致)。
    """
    rnd = random.Random(seed)
    hw = rnd.choice([4, 6, 8, 12, 16])
    mem = rnd.choice([4, 8, 16])
    # 0..15 的固定噪声偏移,注入到 canvas 像素低位
    noise = [rnd.randint(0, 7) for _ in range(8)]
    noise_js = json.dumps(noise)
    return f"""
(() => {{
  try {{
    Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});
  }} catch (e) {{}}
  try {{
    Object.defineProperty(navigator, 'hardwareConcurrency', {{get: () => {hw}}});
  }} catch (e) {{}}
  try {{
    Object.defineProperty(navigator, 'deviceMemory', {{get: () => {mem}}});
  }} catch (e) {{}}
  const NOISE = {noise_js};
  const _gid = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function(...a) {{
    const d = _gid.apply(this, a);
    try {{
      for (let i = 0; i < d.data.length; i += 4) {{
        d.data[i] = (d.data[i] + NOISE[(i >> 2) % NOISE.length]) & 0xff;
      }}
    }} catch (e) {{}}
    return d;
  }};
  const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(...a) {{
    try {{
      const ctx = this.getContext('2d');
      if (ctx) {{
        const d = ctx.getImageData(0, 0, this.width, this.height);
        ctx.putImageData(d, 0, 0);
      }}
    }} catch (e) {{}}
    return _toDataURL.apply(this, a);
  }};
}})();
"""
