from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import feedparser
import httpx

logger = logging.getLogger(__name__)

# 必須アーキテクチャ制約3: HTTP通信の厳格なタイムアウト設定（最大15秒）
HTTP_TIMEOUT_SECONDS = 15.0

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AudioSaleTrackerBot/1.0; "
        "+https://github.com/audio-sale-tracker)"
    ),
    "Accept": "application/rss+xml, application/rdf+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
}

AMAZON_LINK_PATTERN = re.compile(
    r"https?://(?:www\.)?amazon\.co\.jp/(?:[^\s\"'<>]*?dp/[A-Z0-9]{10}|gp/product/[A-Z0-9]{10})"
    r"|https?://amzn\.(?:asia/d|to)/[A-Za-z0-9]+",
    re.IGNORECASE,
)


@dataclass
class RssArticleSnippet:
    source_name: str
    title: str
    url: str
    published: str
    summary: str
    extracted_amazon_links: list[str] = field(default_factory=list)

    def to_prompt_line(self, index: int) -> str:
        clean_summary = re.sub(r"<[^>]+>", " ", self.summary)
        clean_summary = re.sub(r"\s+", " ", clean_summary).strip()
        if len(clean_summary) > 350:
            clean_summary = clean_summary[:350] + "..."
        links_str = (
            "\n    記事内Amazon商品リンク: " + ", ".join(self.extracted_amazon_links[:10])
            if self.extracted_amazon_links
            else ""
        )
        return (
            f"[{index}] 媒体: {self.source_name} | 公開日: {self.published}\n"
            f"    タイトル: {self.title}\n"
            f"    記事URL (source_url): {self.url}\n"
            f"    概要: {clean_summary}{links_str}"
        )


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lowered = text.lower()
    return any(kw.strip().lower() in lowered for kw in keywords if kw.strip())


def _enrich_snippet_from_article(client: httpx.Client, snippet: RssArticleSnippet) -> None:
    """記事本文（メディア・PR記事）からAmazon商品ページ直リンク（/dp/ASIN や amzn.asia）と価格記述を抽出する。
    ※Amazon自体のスクレイピングは行わず、メディア記事内のリンク抽出のみを timeout=15.0 で安全に行う。
    """
    try:
        resp = client.get(snippet.url, timeout=HTTP_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return
        html = resp.text
        found_links = list(dict.fromkeys(AMAZON_LINK_PATTERN.findall(html)))
        if found_links:
            snippet.extracted_amazon_links = found_links[:12]
    except Exception as exc:
        logger.debug("記事本文の補助リンク抽出をスキップ (%s): %s", snippet.url, exc)


def fetch_unread_rss_snippets(
    sources_config: dict[str, Any],
    seen_urls: set[str],
) -> tuple[list[RssArticleSnippet], list[str]]:
    """巡回対象のRSSフィードから未読記事のスニペット一覧を取得する。

    - すべてのHTTPリクエストに timeout=15.0 を明示設定
    - 個別フィードの失敗は try-except で捕捉して継続
    """
    feeds = sources_config.get("rss_feeds", [])
    keywords = sources_config.get("keywords_filter", [])
    max_per_feed = int(sources_config.get("max_rss_items_per_feed", 30))
    max_total = int(sources_config.get("max_total_snippets_for_prompt", 60))

    collected: list[RssArticleSnippet] = []
    newly_checked_urls: list[str] = []

    with httpx.Client(
        timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
    ) as client:
        for feed_info in feeds:
            if not feed_info.get("enabled", True):
                continue
            name = feed_info.get("name", "Unknown Feed")
            url = feed_info.get("url", "").strip()
            if not url:
                continue

            try:
                logger.info("RSS取得開始: %s (%s)", name, url)
                response = client.get(url, timeout=HTTP_TIMEOUT_SECONDS)
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
                entries = getattr(parsed, "entries", [])[:max_per_feed]

                feed_hit_count = 0
                for entry in entries:
                    link = str(entry.get("link", "")).strip()
                    if not link or link in seen_urls or link in newly_checked_urls:
                        continue

                    title = str(entry.get("title", "")).strip()
                    summary = str(
                        entry.get("summary", "") or entry.get("description", "")
                    ).strip()
                    published = str(
                        entry.get("published", "")
                        or entry.get("updated", "")
                        or entry.get("dc_date", "")
                    ).strip()

                    combined_text = f"{title} {summary}"
                    if _matches_keywords(combined_text, keywords):
                        snippet = RssArticleSnippet(
                            source_name=name,
                            title=title,
                            url=link,
                            published=published,
                            summary=summary,
                        )
                        collected.append(snippet)
                        feed_hit_count += 1

                    newly_checked_urls.append(link)

                logger.info(
                    "RSS取得完了: %s (対象候補 %d件 / 新規確認 %d件)",
                    name,
                    feed_hit_count,
                    len(entries),
                )
            except Exception as exc:
                # 1つのフィード取得失敗でパイプライン全体が停止しないよう継続
                logger.warning("RSS取得失敗 (スキップして継続): %s (%s) - %s", name, url, exc)
                continue

        if len(collected) > max_total:
            collected = collected[:max_total]

        # 上位候補記事から記事内のAmazon直リンク（/dp/ASIN 等）を事前抽出
        for snippet in collected[:15]:
            _enrich_snippet_from_article(client, snippet)

    return collected, newly_checked_urls
