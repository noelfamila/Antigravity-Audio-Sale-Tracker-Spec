# Google Antigravity 投入用仕様書：Amazon ハイエンドオーディオ＆イヤーピース セール自動収集・精査サイト構築

以下のテキストをそのまま [Google Antigravity](https://antigravity.google/) のチャット欄に貼り付けるか、プロジェクトルートの `SPEC.md` として配置して実装を依頼してください。

## Antigravity への指示プロンプト（コピー＆ペースト用）

あなたはフルスタックエンジニアおよびAI自動化アーキテクトです。以下の要件定義・アーキテクチャ仕様に基づき、Amazon.co.jpのオーディオ機器およびイヤーピースのセール情報を自動収集・精査し、GitHub Pages上で公開するWebアプリケーションとGitHub Actionsバッチ処理一式を構築してください。

まずはこの仕様書をもとに **Implementation Plan（実装計画書）** を作成して提示してください。

### 1. プロジェクトの目的とゴール

- **目的**：Amazon（Amazon.co.jp）における特定条件のハイエンドオーディオ機器およびイヤーピースのセール・割引・クーポン情報を毎日自動で収集・精査し、製品仕様・価格・割引率・URLリンクを整理した一覧サイト（ダッシュボード）を自動更新する。
- **運用コスト目標**：サーバー代ゼロ（GitHub Actions ＋ GitHub Pages の無料枠、および Gemini API の無料枠〜極小コスト）で完全自動運用できる構成とする。

### 2. 厳密なフィルタリング条件（精査ルール）

Gemini API による情報抽出・精査時に、以下の条件をシステムプロンプトおよび構造化出力（JSON Schema）で厳密に適用すること。

| 区分 | 対象カテゴリ | 価格条件 | 備考・詳細条件 |
| :--- | :--- | :--- | :--- |
| **対象①** | **有線イヤホン（IEM）** | **50,000円以上** | 通常価格または実売価格ベースで5万円以上クラスのリケーブル対応機・ハイエンド有線IEM |
| **対象②** | **有線ヘッドホン** | **50,000円以上** | 開放型・密閉型・平面磁界駆動型などの有線ヘッドホン（5万円以上） |
| **対象③** | **据え置き型 DAC / AMP** | **50,000円以上** | 単体DAC、単体ヘッドホンアンプ、およびDAC/AMP一体型のすべてを含む |
| **対象④** | **ポータブル DAC / AMP** | **50,000円以上** | ドングル型・バッテリー内蔵型を含むポータブル単体DAC、単体AMP、DAC/AMP一体型 |
| **対象⑤** | **イヤーピース** | **価格条件なし（全価格帯）** | 5万円未満を含むすべての有線/TWS向けイヤーピース（シリコン、フォーム、液体シリコン、金属コア等） |
| **除外** | **ワイヤレス製品・低価格機** | **すべて除外** | Bluetoothワイヤレスイヤホン・ヘッドホン（価格不問で除外）、およびイヤーピース以外の5万円未満の製品 |

### 3. 先行事例の失敗を防ぐ5つの必須アーキテクチャ制約（ガードレール）

実装にあたっては、個人開発の自動化パイプラインで頻発する以下のトラブルを確実に回避する設計とすること。

1. **APIリクエストの1回集約（429 レート制限エラーの回避）**
   - 記事ごと・カテゴリごとにループで Gemini API を呼び出す設計は厳禁とする（[Qiitaの先行事例](https://qiita.com/nabeo_ji/items/a4791d9bf36cf3d1beea)および[noteの検証事例](https://note.com/joat/n/nd87e92f71eca)で報告されているクォータ枯渇を防ぐため）。
   - 事前取得したRSSスニペット一覧と検索条件を1つのプロンプトにまとめ、**1回のバッチ実行につき Gemini API 呼び出しは原則1回（フォールバック時でも最大2回）**に収めること。
2. **Amazon直接スクレイピングの禁止（Bot検知・ブロック回避）**
   - Playwright や BeautifulSoup 等で Amazon.co.jp の商品ページを直接大量クロールする処理は実装しないこと（[ZennのBot対策事例](https://zenn.dev/ryota_09/articles/bot-detection-rakuten-automation)と同様、クラウドIPからのアクセスはブロックされるため）。
   - 情報源は **① オーディオ系メディア・プレスリリースのRSSフィード取得**（AV Watch、PHILE WEB、PR TIMES、e☆イヤホンBlog、カジェログ等）と、**② Gemini API の Google検索グラウンディング（[Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search)）** の組み合わせとすること。
3. **HTTP通信の厳格なタイムアウト設定（ジョブのハング防止）**
   - RSS取得やWebページ取得を行うすべてのHTTPリクエスト（`httpx` 等）には **最大15秒のタイムアウト（`timeout=15.0`）** を明示的に設定し、1つのフィード取得失敗でパイプライン全体が停止しないよう `try-except` で継続させること。
4. **Structured Outputs（JSONスキーマ固定）の適用**
   - Google 公式の最新 Python SDK（`google-genai`）を使用し、`tools=[{"google_search": {}}]` と `response_mime_type="application/json"` / `response_schema`（Pydanticモデル）を組み合わせて型安全なJSONを取得すること（[Structured Outputs公式仕様](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)）。
5. **APIキーの秘匿と差分コミット制御**
   - フロントエンド（`docs/` 配下のHTML/JS）にはAPIキーを一切含めず、GitHub Actions が生成した静的JSON（`docs/data/sales.json`）を `fetch()` で読み込むだけの設計とすること（[Zenn「Tsuyu-mi」事例](https://zenn.dev/unsoluble_sugar/articles/beaac7bbf9ee7a)準拠）。
   - データに差分がない日は不要なコミットを行わないよう、ワークフロー内で `git diff --cached --quiet` によるチェックを行うこと。

### 4. ディレクトリ構成とファイル仕様

```text
audio-sale-tracker/
├─ README.md
├─ pyproject.toml              # または requirements.txt (google-genai, pydantic, httpx, feedparser)
├─ .env.example                # GEMINI_API_KEY, GEMINI_MODEL などのサンプル
├─ config/
│  └─ sources.json             # 巡回対象のRSSフィードURL一覧・検索クエリ設定
├─ src/
│  ├─ __init__.py
│  ├─ main.py                  # エントリポイント（RSS取得 → Gemini精査 → JSON更新）
│  ├─ models.py                # Pydantic データモデル定義（SaleItem, SaleDatabase）
│  ├─ rss_fetcher.py           # RSS・記事スニペット取得（timeout=15秒、既読URLフィルタ）
│  ├─ gemini_curator.py        # google-genai SDKによる検索グラウンディング＋JSON精査
│  └─ storage.py               # docs/data/sales.json と state/seen_urls.json の差分保存
├─ state/
│  └─ seen_urls.json           # 処理済み記事URLの履歴（重複処理防止用）
├─ docs/                       # GitHub Pages 公開ルート
│  ├─ index.html               # ダッシュボード画面
│  ├─ styles.css               # ダークモード基調のモダンなレスポンシブスタイル
│  ├─ app.js                   # カテゴリ絞り込み・価格/割引率ソート・検索UIロジック
│  └─ data/
│     └─ sales.json            # フロントエンドが読み込むセール製品データ正本
└─ .github/
   └─ workflows/
      └─ daily_update.yml      # 毎日JST 9:00（UTC 0:00）実行＋GitHub Pagesデプロイ
```

### 5. データモデル定義（`src/models.py`）

Pydantic で以下のスキーマを定義し、Gemini API の `response_schema` に渡すこと。

```python
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
```

### 6. フロントエンド UI 要件（`docs/`）

外部フレームワークのビルドステップなしでも高速に動作するクリーンな Vanilla JS + CSS（または軽量構成）で、`docs/` 配下に以下を実装すること。

- **ヘッダーエリア**：
  - 最終更新日時（`updated_at`）の表示
  - 大型セール開催情報・次回予告バナー（`upcoming_sale_notice` がある場合に表示し、参照URLへリンク）
- **フィルタ・検索バー**：
  - カテゴリ切り替えタブ：「すべて」「有線イヤホン (5万円〜)」「有線ヘッドホン (5万円〜)」「据え置きDAC/AMP (5万円〜)」「ポータブルDAC/AMP (5万円〜)」「イヤーピース (全価格帯)」
  - ソート切り替え：「新着順」「割引率が高い順」「価格が高い順 / 安い順」
  - フリーワード検索ボックス（ブランド名・型番・DACチップ名などで即時フィルタ）
- **製品カード表示**：
  - 新着アイテム（直近3日以内に検出）には `NEW` バッジを表示
  - カテゴリバッジ、ブランド名・製品名、スペック概要（`specs_summary`）
  - 通常価格（取り消し線）、セール価格、割引率バッジ（強調カラー）
  - **2つのリンクボタン**：「製品ページ / Amazonを見る（`product_url`）」「情報元記事を見る（`source_url`）」を必ずクリック可能なボタンとして配置

### 7. GitHub Actions ワークフロー要件（`.github/workflows/daily_update.yml`）

- **トリガー**：
  - `schedule`: `- cron: '0 0 * * *'`（毎日 UTC 0:00 ＝ 日本時間 朝9:00）
  - `workflow_dispatch`: GitHub画面からの手動テスト実行に対応
- **環境変数・Secrets**：
  - `GEMINI_API_KEY`: `${{ secrets.GEMINI_API_KEY }}`
  - `GEMINI_MODEL`: デフォルトは `gemini-2.5-flash` または `gemini-3-flash`（無料枠・検索グラウンディング対応モデル）
- **処理ステップ**：
  1. リポジトリのチェックアウト（`actions/checkout@v4`）
  2. Python 3.11 セットアップ（`actions/setup-python@v5`）と依存パッケージのインストール
  3. `python -m src.main` の実行（情報収集・精査・`docs/data/sales.json` 更新）
  4. `docs/data/sales.json` および `state/seen_urls.json` に変更があれば自動コミット＆Push
  5. GitHub Pages へのデプロイ（`actions/upload-pages-artifact@v3` および `actions/deploy-pages@v4`）

### 8. 初期データの同梱と検証タスク

1. クローン直後や初回デプロイ時にも画面レイアウトを確認できるよう、`docs/data/sales.json` にサンプルデータ（SHANLING AE10、SHANLING HW600、iBasso Audio D16 TAIPAN、iBasso Audio Nunchaku、SHANLING EH2、および定番イヤーピースのサンプル）を初期配置すること。
2. ローカルでの動作確認コマンド、Google AI Studio での `GEMINI_API_KEY` 取得手順、および GitHub Secrets と GitHub Pages の有効化手順を `README.md` に日本語で明記すること。
