from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models import CurationResponse, SaleDatabase, SaleItem

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def load_seen_urls(seen_urls_path: Path) -> set[str]:
    if not seen_urls_path.exists():
        return set()
    try:
        data = json.loads(seen_urls_path.read_text(encoding="utf-8"))
        urls = data.get("seen_urls", [])
        return {str(u).strip() for u in urls if str(u).strip()}
    except Exception as exc:
        logger.warning("seen_urls.json の読み込みに失敗しました（空として扱います）: %s", exc)
        return set()


def save_seen_urls(
    seen_urls_path: Path,
    existing_urls: set[str],
    newly_checked_urls: list[str],
    max_history: int = 1000,
) -> bool:
    """処理済みURLを保存する。内容に変化があった場合のみファイルを更新して True を返す。"""
    ordered_urls: list[str] = []
    if seen_urls_path.exists():
        try:
            raw = json.loads(seen_urls_path.read_text(encoding="utf-8"))
            ordered_urls = [str(u).strip() for u in raw.get("seen_urls", []) if str(u).strip()]
        except Exception:
            ordered_urls = sorted(existing_urls)
    else:
        ordered_urls = sorted(existing_urls)

    known = set(ordered_urls)
    added_count = 0
    for url in newly_checked_urls:
        clean = url.strip()
        if clean and clean not in known:
            ordered_urls.append(clean)
            known.add(clean)
            added_count += 1

    if len(ordered_urls) > max_history:
        ordered_urls = ordered_urls[-max_history:]

    if added_count == 0 and seen_urls_path.exists():
        return False

    seen_urls_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "seen_urls": ordered_urls,
    }
    seen_urls_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("seen_urls.json を更新しました (新規追加: %d件, 合計: %d件)", added_count, len(ordered_urls))
    return True


def load_sales_database(sales_json_path: Path) -> SaleDatabase:
    if not sales_json_path.exists():
        return SaleDatabase(
            updated_at=datetime.now(JST).isoformat(timespec="seconds"),
            upcoming_sale_notice=None,
            upcoming_sale_url=None,
            items=[],
        )
    try:
        raw = json.loads(sales_json_path.read_text(encoding="utf-8"))
        return SaleDatabase.model_validate(raw)
    except Exception as exc:
        logger.warning("sales.json の読み込みに失敗しました（新規作成します）: %s", exc)
        return SaleDatabase(
            updated_at=datetime.now(JST).isoformat(timespec="seconds"),
            upcoming_sale_notice=None,
            upcoming_sale_url=None,
            items=[],
        )


def _items_are_equal(a: list[SaleItem], b: list[SaleItem]) -> bool:
    if len(a) != len(b):
        return False
    return [item.model_dump() for item in a] == [item.model_dump() for item in b]


def update_sales_database(
    sales_json_path: Path,
    curation: CurationResponse,
    max_items: int = 120,
) -> bool:
    """精査結果を既存の sales.json にマージする。
    実質的なデータ差分（アイテムの追加・更新、またはセール告知の変更）がある場合のみ書き込んで True を返す。
    これにより差分がない日の不要なコミットを防止する（必須制約5）。
    """
    current_db = load_sales_database(sales_json_path)

    # 既存アイテムをIDマップ化（検索結果URLなどの旧無効データは除外し、新規検出分があれば最新情報で上書き）
    merged_by_id: dict[str, SaleItem] = {
        item.id: item
        for item in current_db.items
        if "amazon.co.jp/s?" not in item.product_url and "amazon.co.jp/s/" not in item.product_url
    }
    for new_item in curation.items:
        merged_by_id[new_item.id] = new_item

    # 検出日の新しい順 → ブランド名順にソート
    sorted_items = sorted(
        merged_by_id.values(),
        key=lambda x: (x.detected_date, x.brand.lower(), x.product_name.lower()),
        reverse=True,
    )[:max_items]

    new_notice = (
        curation.upcoming_sale_notice
        if curation.upcoming_sale_notice is not None
        else current_db.upcoming_sale_notice
    )
    new_notice_url = (
        curation.upcoming_sale_url
        if curation.upcoming_sale_url is not None
        else current_db.upcoming_sale_url
    )

    has_data_changed = (
        not _items_are_equal(current_db.items, sorted_items)
        or current_db.upcoming_sale_notice != new_notice
        or current_db.upcoming_sale_url != new_notice_url
        or not sales_json_path.exists()
    )

    if not has_data_changed:
        logger.info("sales.json にデータ差分はありません（更新スキップ）。")
        return False

    updated_db = SaleDatabase(
        updated_at=datetime.now(JST).isoformat(timespec="seconds"),
        upcoming_sale_notice=new_notice,
        upcoming_sale_url=new_notice_url,
        items=sorted_items,
    )

    sales_json_path.parent.mkdir(parents=True, exist_ok=True)
    sales_json_path.write_text(
        json.dumps(updated_db.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("sales.json を更新しました (総アイテム数: %d件)", len(sorted_items))
    return True
