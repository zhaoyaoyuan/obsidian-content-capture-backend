from __future__ import annotations

from script.douyin_resolver import (
    DouyinResolveError,
    _meta_from_aweme_item,
    expand_share_url,
)

# 桌面版 Chrome UA：抖音前端会基于此 UA 生成 uifid / a_bogus 等签名
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
)

_DETAIL_API_HINTS = (
    "aweme/v1/web/aweme/detail",
    "web/api/v2/aweme/iteminfo",
    "aweme/detail",
)


def _is_detail_api(url: str) -> bool:
    return any(hint in url for hint in _DETAIL_API_HINTS)


def resolve_via_browser(share_text: str, *, headless: bool = True, timeout: int = 60):
    """用 Playwright 加载分享页并拦截前端发出的作品详情 API 响应。

    适用于抖音不再把作品数据 SSR 注入分享页 HTML 的场景：
    浏览器在加载页面时会自动带上 uifid / a_bogus 等签名去请求详情接口，
    我们直接拦截该 JSON 响应即可拿到完整作品数据（无需手动登录 Cookie）。

    依赖：playwright + chromium
        pip install playwright && playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise DouyinResolveError(
            "浏览器解析需要 playwright，请先执行："
            "pip install playwright && playwright install chromium"
        ) from exc

    share_url = expand_share_url(share_text)

    captured: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=_BROWSER_UA,
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        def on_response(resp):
            if not _is_detail_api(resp.url):
                return
            try:
                body = resp.json()
            except Exception:
                return
            item_list = body.get("item_list") or (
                [body["aweme_detail"]] if body.get("aweme_detail") else []
            )
            if item_list:
                captured[resp.url] = item_list[0]

        page.on("response", on_response)

        # 在导航前已挂载监听器，页面加载过程中发出的详情请求都会被捕获
        try:
            page.goto(share_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            # 抖音页面常因持续轮询导致 networkidle 超时，domcontentloaded 即可
            pass

        # 兜底等待：若导航期间尚未发出详情请求，则显式等待一次
        if not captured:
            try:
                page.wait_for_response(_is_detail_api, timeout=timeout * 1000)
            except Exception:
                pass
            page.wait_for_timeout(3000)

        browser.close()

    if not captured:
        raise DouyinResolveError(
            "浏览器加载分享页后未捕获到作品详情接口响应，链接可能已失效或需登录。"
        )

    # 取最新一条响应（页面可能多次轮询，取最后一份最完整）
    item = list(captured.values())[-1]
    return _meta_from_aweme_item(item, share_url)
