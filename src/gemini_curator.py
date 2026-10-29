from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from google import genai
from google.genai import types

from src.models import AudioCategory, CurationResponse, SaleItem
from src.rss_fetcher import HTTP_TIMEOUT_SECONDS, RssArticleSnippet

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
HIGH_END_MIN_PRICE_JPY = 50_000

ASIN_EXTRACTOR = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)

SYSTEM_INSTRUCTION = """\
あなたはAmazon.co.jpのハイエンドオーディオ機器およびイヤーピース専門のセール情報キュレーターです。
提供されたRSS記事スニペット一覧（および記事内から抽出されたAmazon直リンク）と、
Google検索グラウンディング（Grounding with Google Search）の結果を組み合わせて、
現在開催中または直近で発表されたAmazon.co.jpのセール・割引・クーポン対象製品を抽出・精査してください。

【厳守すべきフィルタリング条件（精査ルール）】
以下の5区分のいずれかに合致する製品のみを `items` に含めてください。

1. `wired_iem`（有線イヤホン / IEM）：
   - 通常価格またはセール価格が **50,000円（税込）以上** の有線イヤホン（リケーブル対応機・ハイエンドIEM）のみ。
2. `wired_headphone`（有線ヘッドホン）：
   - 通常価格またはセール価格が **50,000円（税込）以上** の有線ヘッドホン（開放型・密閉型・平面磁界駆動型など）のみ。
3. `desktop_dac_amp`（据え置き型 DAC / AMP）：
   - 通常価格またはセール価格が **50,000円（税込）以上** の据え置き型単体DAC、単体ヘッドホンアンプ、およびDAC/AMP一体型。
4. `portable_dac_amp`（ポータブル DAC / AMP）：
   - 通常価格またはセール価格が **50,000円（税込）以上** のドングル型・バッテリー内蔵型を含むポータブル単体DAC、単体AMP、DAC/AMP一体型。
5. `earpiece`（イヤーピース）：
   - **価格条件なし（全価格帯対象）**。5万円未満を含むすべての有線イヤホン用・TWS用交換イヤーピース（シリコン、フォーム、液体シリコン、金属コア等）。

【完全除外ルール（絶対に `items` に含めないこと）】
- Bluetoothワイヤレスイヤホン（完全ワイヤレスイヤホン / TWS）・ワイヤレスヘッドホン本体は、価格を問わず **すべて除外** してください。
  （※ただし、交換用イヤーピース製品が「TWS対応」と記載されている場合は `earpiece` として対象に含めます）
- `earpiece` 以外のカテゴリで、通常価格・セール価格ともに 50,000円未満の低価格・エントリー〜ミドル機は **すべて除外** してください。
- DAP（デジタルオーディオプレーヤー単体）やケーブル単体は、上記5カテゴリに該当しないため除外してください。

【URLおよび価格の厳密性ルール（最重要）】
- `product_url`:
  - 必ず個別製品のAmazon商品ページ直リンク（`https://www.amazon.co.jp/dp/B0...` または記事内の `https://amzn.asia/d/...`）を設定してください。
  - `https://www.amazon.co.jp/s?k=...` のようなAmazon検索結果一覧ページのURLは設定しないでください。
- `source_url`:
  - 必ずセール情報の根拠となった個別記事・プレスリリースの完全なURL（例: `https://musinltd.com/News/1471.html` や `https://av.watch.impress.co.jp/docs/news/...`）を設定してください。
  - サイトのトップページ（`https://av.watch.impress.co.jp/` など）だけを `source_url` に設定することは厳禁です。
- `regular_price_jpy` / `sale_price_jpy` / `discount_rate`:
  - 記事や公式発表に記載されている正確な金額・割引率と完全に一致させてください（架空の価格を推測・捏造しないこと）。
  - 通常価格と割引率（例: 25%OFF）が判明している場合は、計算が整合するセール価格を設定してください。
"""


def _build_single_shot_prompt(
    snippets: list[RssArticleSnippet],
    search_queries: list[str],
    today_str: str,
) -> str:
    snippet_block = (
        "\n\n".join(s.to_prompt_line(i + 1) for i, s in enumerate(snippets))
        if snippets
        else "（本日の未読RSS記事スニペットはありません。Google検索グラウンディングを使用して最新セール情報を探索してください。）"
    )
    queries_block = "\n".join(f"- {q}" for q in search_queries)

    return f"""\
本日の日付: {today_str} (JST)

以下の【1. 巡回RSSから取得した未読記事スニペット】および【2. Google検索グラウンディングでの重点調査トピック】をもとに、
Amazon.co.jp におけるハイエンドオーディオ機器（5万円以上の有線IEM / 有線ヘッドホン / 据え置きDAC・AMP / ポータブルDAC・AMP）
およびイヤーピース（全価格帯）のセール・割引・クーポン情報を精査し、指定のJSONスキーマで出力してください。

また、直近のAmazon大型セール（スマイルSALE、プライム感謝祭、ブラックフライデー、タイムセール祭り等）の開催状況や次回予告があれば
`upcoming_sale_notice` と `upcoming_sale_url` に記載してください。

【1. 巡回RSSから取得した未読記事スニペット】
{snippet_block}

【2. Google検索グラウンディングでの重点調査トピック】
{queries_block}
"""


def _slugify(brand: str, product_name: str, existing_id: str = "") -> str:
    base = existing_id.strip().lower() if existing_id.strip() else f"{brand}-{product_name}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return slug or "audio-item"


def _normalize_amazon_product_url(url: str) -> str:
    """短縮URL (amzn.asia / amzn.to) を展開し、Amazon商品ページURLを正規の https://www.amazon.co.jp/dp/<ASIN> に整形する。"""
    clean_url = url.strip()
    if not clean_url:
        return clean_url

    # 短縮URLの場合はリダイレクト先を解決 (timeout=15.0)
    if re.match(r"^https?://amzn\.(?:asia/d|to)/", clean_url, re.IGNORECASE):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = client.get(clean_url, timeout=HTTP_TIMEOUT_SECONDS)
                clean_url = str(resp.url)
        except Exception as exc:
            logger.debug("短縮URLの展開に失敗 (%s): %s", clean_url, exc)

    asin_match = ASIN_EXTRACTOR.search(clean_url)
    if asin_match:
        asin = asin_match.group(1).upper()
        return f"https://www.amazon.co.jp/dp/{asin}"

    return clean_url


def _is_top_page_only_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        path = (parsed.path or "").strip("/")
        return not path and not parsed.query
    except Exception:
        return True


def _reconcile_prices(item: SaleItem) -> None:
    """通常価格と割引率（%OFF）からセール価格の未入力や計算ズレを補正する。"""
    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*OFF", item.discount_rate or "", re.IGNORECASE)
    if not pct_match:
        return
    pct = float(pct_match.group(1))
    if not (0 < pct < 95):
        return

    if item.regular_price_jpy and item.regular_price_jpy > 0 and item.sale_price_jpy is None:
        item.sale_price_jpy = int(round(item.regular_price_jpy * (1.0 - pct / 100.0)))


def _verify_urls_and_sync_price(item: SaleItem) -> bool:
    """product_url と source_url のリンク切れ(404)を検証し、Amazon商品ページから現在価格が取得できた場合は表示価格を同期する。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja-JP,ja;q=0.9",
    }
    with httpx.Client(
        timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        follow_redirects=True,
        headers=headers,
    ) as client:
        # 1. 情報元記事URLの疎通チェック
        try:
            s_resp = client.get(item.source_url, timeout=HTTP_TIMEOUT_SECONDS)
            if s_resp.status_code in (404, 410):
                logger.info("除外 (source_url が %d リンク切れ): %s", s_resp.status_code, item.source_url)
                return False
        except Exception as exc:
            logger.debug("source_url 疎通確認スキップ (%s): %s", item.source_url, exc)

        # 2. 商品ページURLの疎通＆実売価格同期チェック
        try:
            p_resp = client.get(item.product_url, timeout=HTTP_TIMEOUT_SECONDS)
            if p_resp.status_code in (404, 410):
                logger.info("除外 (product_url が %d リンク切れ): %s", p_resp.status_code, item.product_url)
                return False
            if p_resp.status_code == 200 and "amazon.co.jp" in str(p_resp.url):
                price_m = re.search(r'class="a-price-whole"[^>]*>([0-9,]+)<', p_resp.text)
                if price_m:
                    live_price = int(price_m.group(1).replace(",", ""))
                    if live_price > 0:
                        item.sale_price_jpy = live_price
                        if item.regular_price_jpy and item.regular_price_jpy > live_price:
                            calc_pct = int(
                                round((item.regular_price_jpy - live_price) / item.regular_price_jpy * 100)
                            )
                            if calc_pct > 0 and not item.discount_rate:
                                item.discount_rate = f"{calc_pct}%OFF"
        except Exception as exc:
            logger.debug("product_url 疎通確認スキップ (%s): %s", item.product_url, exc)

    return True


def _validate_and_filter_items(items: list[SaleItem], today_str: str) -> list[SaleItem]:
    """Pydanticパース後のセーフティガード（URL正規化、価格整合性補正、5万円未満の非イヤーピースやワイヤレス混入を排除）"""
    wireless_ng_words = [
        "完全ワイヤレス",
        "ワイヤレスイヤホン",
        "ワイヤレスヘッドホン",
        "bluetoothイヤホン",
        "bluetoothヘッドホン",
        "ノイキャンワイヤレス",
        "bluetoothレシーバー",
    ]
    valid_items: list[SaleItem] = []

    for item in items:
        item.id = _slugify(item.brand, item.product_name, item.id)
        if not item.detected_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", item.detected_date):
            item.detected_date = today_str

        # Amazon商品URLの正規化（/dp/ASIN 形式へ統一）
        item.product_url = _normalize_amazon_product_url(item.product_url)

        # 検索結果URL (/s?k=...) やトップページのみの不完全なURLは除外
        if "amazon.co.jp/s?" in item.product_url or "amazon.co.jp/s/" in item.product_url:
            logger.info("除外 (個別商品ページではなく検索URLのため): %s %s (%s)", item.brand, item.product_name, item.product_url)
            continue

        if _is_top_page_only_url(item.source_url):
            logger.info("除外 (情報元記事URLがトップページのみのため): %s %s (%s)", item.brand, item.product_name, item.source_url)
            continue

        # 通常価格と割引率の整合性補正
        _reconcile_prices(item)

        # リンク切れ(404)チェック＆Amazon実売価格同期
        if not _verify_urls_and_sync_price(item):
            continue

        # イヤーピース以外のカテゴリはワイヤレス製品と5万円未満を厳格に除外
        if item.category != AudioCategory.EARPIECE:
            combined_text = f"{item.product_name} {item.specs_summary}".lower()
            if any(ng in combined_text for ng in wireless_ng_words):
                logger.info("除外 (ワイヤレス判定): %s %s", item.brand, item.product_name)
                continue

            # 価格条件チェック: 通常価格・セール価格の少なくとも一方が判明している場合、最大価格が50,000円以上であること
            known_prices = [
                p for p in (item.regular_price_jpy, item.sale_price_jpy) if p is not None and p > 0
            ]
            if known_prices and max(known_prices) < HIGH_END_MIN_PRICE_JPY:
                logger.info(
                    "除外 (5万円未満判定: %s円): %s %s",
                    max(known_prices),
                    item.brand,
                    item.product_name,
                )
                continue

        valid_items.append(item)

    return valid_items


def curate_sales_with_gemini(
    snippets: list[RssArticleSnippet],
    sources_config: dict[str, Any],
) -> CurationResponse:
    """必須アーキテクチャ制約1 & 4:
    - google-genai SDKを使用
    - 1回のバッチ実行につき Gemini API 呼び出しは原則1回（フォールバック時でも最大2回）
    - Google検索グラウンディング + Structured Outputs (CurationResponse)
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "環境変数 GEMINI_API_KEY が設定されていません。"
            ".env または GitHub Secrets に設定してください。"
        )

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    search_queries = sources_config.get("search_queries", [])
    prompt = _build_single_shot_prompt(snippets, search_queries, today_str)

    client = genai.Client(api_key=api_key)

    # 1回目の呼び出し（原則1回で完結）: Google Search Grounding + Structured Outputs
    try:
        logger.info("Gemini API 精査リクエスト実行 (1/1): model=%s, snippets=%d", model_name, len(snippets))
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[{"google_search": {}}],
                response_mime_type="application/json",
                response_schema=CurationResponse,
                temperature=0.1,
            ),
        )
        if response.parsed is not None and isinstance(response.parsed, CurationResponse):
            result = response.parsed
        elif response.text:
            result = CurationResponse.model_validate_json(response.text)
        else:
            result = CurationResponse()

        result.items = _validate_and_filter_items(result.items, today_str)
        logger.info("Gemini API 精査完了 (1回で成功): 抽出件数=%d件", len(result.items))
        return result

    except Exception as first_exc:
        # フォールバック（最大2回目まで）:
        logger.warning(
            "1回目のGemini呼び出しで例外が発生したためフォールバックを実行します (2/2): %s",
            first_exc,
        )
        fallback_response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=CurationResponse,
                temperature=0.1,
            ),
        )
        if fallback_response.parsed is not None and isinstance(
            fallback_response.parsed, CurationResponse
        ):
            result = fallback_response.parsed
        elif fallback_response.text:
            result = CurationResponse.model_validate_json(fallback_response.text)
        else:
            result = CurationResponse()

        result.items = _validate_and_filter_items(result.items, today_str)
        logger.info("Gemini API フォールバック精査完了 (計2回): 抽出件数=%d件", len(result.items))
        return result
