"""自检脚本(离线):验证签名原语 + 关键依赖是否就位。
  python selftest.py
注:抓取/登录现在走真实浏览器(Playwright),不再依赖自算 a_bogus,
   所以这里只做基础原语自检 + Playwright 可用性检查。
"""
import sys

from app.platforms.douyin.signing import sm3_hash, rc4


def check_primitives():
    h = sm3_hash(b"abc").hex()
    assert h == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0", "SM3 失败"
    print("[SM3] ✓")
    out = bytes(rc4(b"Key", b"Plaintext")).hex().upper()
    assert out == "BBF316E8D940AF0AD3", "RC4 失败"
    print("[RC4] ✓")


def check_playwright():
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[Playwright] ✗ 未安装: {e}")
        print("   pip install -r requirements.txt && playwright install chromium")
        return
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        print("[Playwright] ✓ Chromium 可启动")
    except Exception as e:
        print(f"[Playwright] ✗ 启动失败: {e}")
        print("   多半是浏览器没装,执行:  playwright install chromium")


if __name__ == "__main__":
    check_primitives()
    check_playwright()
    print("\n自检完成。启动:  uvicorn app.main:app --port 8000")
