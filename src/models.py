from enum import Enum
from pydantic import BaseModel, Field


class AudioCategory(str, Enum):
    WIRED_IEM = "wired_iem"                  # 有線イヤホン（5万円以上）
    WIRED_HEADPHONE = "wired_headphone"      # 有線ヘッドホン（5万円以上）
    DESKTOP_DAC_AMP = "desktop_dac_amp"      # 据え置きDAC/AMP（単体・一体型、5万円以上）
    PORTABLE_DAC_AMP = "portable_dac_amp"    # ポータブルDAC/AMP（単体・一体型、5万円以上）
    EARPIECE = "earpiece"                    # イヤーピース（価格不問）


class SaleItem(BaseModel):
    id: str = Field(description="ブランド名と製品型番から生成する一意のslug（例: shanling-ae10）")
    brand: str = Field(description="メーカー・ブランド名（例: SHANLING, iBasso Audio, SENNHEISER）")
    product_name: str = Field(description="製品名・型番（例: AE10, HW600, D16 TAIPAN）")
    category: AudioCategory = Field(description="製品カテゴリ")
    specs_summary: str = Field(description="ドライバー構成、DACチップ、出力端子、素材などの主要スペック要約（1〜2文）")
    regular_price_jpy: int | None = Field(default=None, description="通常価格・参考価格（税込・円、判明している場合）")
    sale_price_jpy: int | None = Field(default=None, description="セール価格（税込・円、判明している場合）")
    discount_rate: str = Field(description="割引率やクーポン・ポイント還元内容（例: 25%OFF, 10,000円OFFクーポン）")
    sale_name: str = Field(description="セール名称（例: Amazon スマイルSALE, タイムセール, プライム感謝祭）")
    product_url: str = Field(description="Amazon商品ページまたはメーカー・販売店公式製品ページのURL")
    source_url: str = Field(description="セール情報の参照元記事・プレスリリースのURL")
    detected_date: str = Field(description="検出日（YYYY-MM-DD）")


class CurationResponse(BaseModel):
    upcoming_sale_notice: str | None = Field(default=None, description="次回大型セール予告や開催中セールの全体概要（あれば記載）")
    upcoming_sale_url: str | None = Field(default=None, description="大型セール予告記事の参照URL")
    items: list[SaleItem] = Field(default_factory=list, description="条件に合致したセール製品リスト")


class SaleDatabase(BaseModel):
    updated_at: str = Field(description="最終更新日時（ISO 8601形式）")
    upcoming_sale_notice: str | None = Field(default=None, description="次回大型セール予告や開催中セールの全体概要")
    upcoming_sale_url: str | None = Field(default=None, description="大型セール予告記事の参照URL")
    items: list[SaleItem] = Field(default_factory=list, description="公開中のセール製品リスト")
