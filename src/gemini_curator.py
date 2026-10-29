from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from google import genai
from google.genai import types

from src.models import AudioCategory, CurationResponse, SaleItem
from src.rss_fetcher import RssArticleSnippet

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
HIGH_END_MIN_PRICE_JPY = 50_000

SYSTEM_INSTRUCTION = """\
あなたはAmazon.co.jpのハイエンドオーディオ機器およびイヤーピース専門のセール情報キュレーターです。
提供されたRSS記事スニペット一覧と、Google検索グラウンディング（Grounding with Google Search）の結果を組み合わせて、
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

【出力データ作成ルール】
- `id`: ブランド名と型番から英小文字とハイフンのみで一意のslugを生成してください（例: `shanling-ae10`, `ibasso-d16-taipan`, `azla-sednaearfit-crystal-2`）。
- `specs_summary`: ドライバー構成、搭載DACチップ、アンプ回路、出力端子（4.4mmバランス等）、イヤーピース素材などの主要スペックを1〜2文で簡潔・正確に要約してください。
- `product_url`: Amazon.co.jpの商品ページURL（`https://www.amazon.co.jp/...`）または代理店・メーカー公式ページURLを設定してください。
- `source_url`: セール情報の根拠となった記事・プレスリリースのURLを設定してください。
- `detected_date`: 検出日（YYYY-MM-DD形式）を設定してください。
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


def _validate_and_filter_items(items: list[SaleItem], today_str: str) -> list[SaleItem]:
    """Pydanticパース後のセーフティガード（5万円未満の非イヤーピースやワイヤレス混入をコード側でも排除）"""
    wireless_ng_words = [
        "完全ワイヤレス",
        "ワイヤレスイヤホン",
        "ワイヤレスヘッドホン",
        "bluetoothイヤホン",
        "bluetoothヘッドホン",
        "ノイキャンワイヤレス",
    ]
    valid_items: list[SaleItem] = []

    for item in items:
        item.id = _slugify(item.brand, item.product_name, item.id)
        if not item.detected_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", item.detected_date):
            item.detected_date = today_str

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
                temperature=0.2,
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
        # モデルやAPIエンドポイント側で tools=[{"google_search": {}}] と response_mime_type="application/json" の
        # 同時指定が拒否された場合、またはJSONパースに失敗した場合に構造化出力のみで整形を行う（合計最大2回）
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
