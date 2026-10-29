from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from src.gemini_curator import curate_sales_with_gemini
from src.rss_fetcher import fetch_unread_rss_snippets
from src.storage import load_seen_urls, save_seen_urls, update_sales_database

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "sources.json"
SEEN_URLS_PATH = PROJECT_ROOT / "state" / "seen_urls.json"
SALES_JSON_PATH = PROJECT_ROOT / "docs" / "data" / "sales.json"


def _load_dotenv_if_present(dotenv_path: Path) -> None:
    """外部パッケージを増やさずにローカル開発用の .env ファイルを読み込む。"""
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Amazon ハイエンドオーディオ＆イヤーピース セール自動収集・精査バッチ"
    )
    parser.add_argument(
        "--rss-only",
        action="store_true",
        help="RSSフィードの取得テストのみを実行し、Gemini API呼び出しとファイル保存をスキップします",
    )
    parser.add_argument(
        "--force-curate",
        action="store_true",
        help="未読RSS記事が0件の場合でも、Google検索グラウンディングによるセール探索を実行します",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("src.main")

    _load_dotenv_if_present(PROJECT_ROOT / ".env")

    if not CONFIG_PATH.exists():
        logger.error("設定ファイルが見つかりません: %s", CONFIG_PATH)
        return 1

    sources_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    seen_urls = load_seen_urls(SEEN_URLS_PATH)

    # 1. RSSフィードから未読記事スニペットを取得 (timeout=15.0秒)
    snippets, newly_checked_urls = fetch_unread_rss_snippets(sources_config, seen_urls)
    logger.info(
        "RSS巡回結果: セール関連候補スニペット=%d件, 新規確認URL=%d件",
        len(snippets),
        len(newly_checked_urls),
    )

    if args.rss_only:
        for idx, snippet in enumerate(snippets, start=1):
            print(snippet.to_prompt_line(idx))
        return 0

    if not snippets and not args.force_curate:
        logger.info(
            "新規のセール関連RSSスニペットがないため、Google検索グラウンディング単独での定期精査を実行します。"
        )

    # 2. Gemini API (Google Search Grounding + Structured Outputs) で1回集約精査
    curation_result = curate_sales_with_gemini(snippets, sources_config)

    # 3. 精査結果と既読URLの保存
    sales_updated = update_sales_database(SALES_JSON_PATH, curation_result)
    max_history = int(sources_config.get("max_seen_urls_history", 1000))
    seen_updated = save_seen_urls(
        SEEN_URLS_PATH,
        seen_urls,
        newly_checked_urls,
        max_history=max_history,
    )

    logger.info(
        "バッチ処理完了: sales_updated=%s, seen_updated=%s",
        sales_updated,
        seen_updated,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
