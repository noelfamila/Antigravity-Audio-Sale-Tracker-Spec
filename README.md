# Audio Sale Tracker — Amazon ハイエンドオーディオ＆イヤーピース セール自動収集・精査サイト

Amazon.co.jp における**ハイエンド有線オーディオ機器（50,000円以上）**および**交換用イヤーピース（全価格帯）**のセール・割引・クーポン情報を毎日自動で収集・精査し、GitHub Pages 上のダッシュボードを更新するサーバーレスWebアプリケーション＆バッチパイプラインです。

---

## 1. 精査ルール（フィルタリング条件）

| 区分 | カテゴリID | 対象カテゴリ | 価格条件 | 備考・詳細条件 |
| :--- | :--- | :--- | :--- | :--- |
| **対象①** | `wired_iem` | **有線イヤホン（IEM）** | **50,000円以上** | 通常価格または実売価格ベースで5万円以上クラスのリケーブル対応機・ハイエンド有線IEM |
| **対象②** | `wired_headphone` | **有線ヘッドホン** | **50,000円以上** | 開放型・密閉型・平面磁界駆動型などの有線ヘッドホン（5万円以上） |
| **対象③** | `desktop_dac_amp` | **据え置き型 DAC / AMP** | **50,000円以上** | 単体DAC、単体ヘッドホンアンプ、およびDAC/AMP一体型のすべてを含む |
| **対象④** | `portable_dac_amp` | **ポータブル DAC / AMP** | **50,000円以上** | ドングル型・バッテリー内蔵型を含むポータブル単体DAC、単体AMP、DAC/AMP一体型 |
| **対象⑤** | `earpiece` | **イヤーピース** | **価格条件なし（全価格帯）** | 5万円未満を含むすべての有線/TWS向けイヤーピース（シリコン、フォーム、液体シリコン、金属コア等） |
| **除外** | — | **ワイヤレス製品・低価格機** | **すべて除外** | Bluetoothワイヤレスイヤホン・ヘッドホン（価格不問で除外）、およびイヤーピース以外の5万円未満の製品 |

---

## 2. アーキテクチャの特徴（5つの必須ガードレール）

1. **APIリクエストの1回集約（429 レート制限エラーの回避）**
   - 未読RSS記事スニペットと検索トピックを1つのプロンプトに集約し、**1回のバッチ実行につき Gemini API 呼び出しを原則1回（フォールバック時でも最大2回）**に制限しています。
2. **Amazon直接スクレイピングの禁止（Bot検知・ブロック回避）**
   - Amazon商品ページの直接スクレイピングは行わず、オーディオ系メディア・PRのRSSフィード（AV Watch、PHILE WEB、PR TIMES、e☆イヤホンBlog、カジェログ等）と **Gemini API の Google検索グラウンディング（`Grounding with Google Search`）** のみで情報を取得します。
3. **HTTP通信の厳格なタイムアウト設定（ジョブのハング防止）**
   - `src/rss_fetcher.py` のすべての `httpx` リクエストに **`timeout=15.0`（15秒）** を設定し、個別フィードの障害時はスキップして処理を継続します。
4. **Structured Outputs（JSONスキーマ固定）**
   - 公式SDK `google-genai` と Pydantic モデル（`CurationResponse` / `SaleItem`）により、型安全なJSON出力を保証します。
5. **APIキーの秘匿と差分コミット制御**
   - フロントエンド（`docs/`）にはAPIキーを一切含めず、静的JSON（`docs/data/sales.json`）を `fetch()` する設計です。データ差分がない日は `git diff --cached --quiet` により不要なコミットをスキップします。

---

## 3. ディレクトリ構成

```text
.
├─ README.md
├─ pyproject.toml              # Pythonプロジェクト設定
├─ requirements.txt            # 依存パッケージ一覧 (google-genai, pydantic, httpx, feedparser)
├─ .env.example                # ローカル実行用環境変数テンプレート
├─ config/
│  └─ sources.json             # 巡回対象のRSSフィードURL一覧・検索クエリ設定
├─ src/
│  ├─ __init__.py
│  ├─ main.py                  # エントリポイント（RSS取得 → Gemini精査 → JSON更新）
│  ├─ models.py                # Pydantic データモデル定義（AudioCategory, SaleItem, CurationResponse, SaleDatabase）
│  ├─ rss_fetcher.py           # RSS・記事スニペット取得（timeout=15秒、既読URLフィルタ）
│  ├─ gemini_curator.py        # google-genai SDKによる検索グラウンディング＋JSON精査
│  └─ storage.py               # docs/data/sales.json と state/seen_urls.json の差分保存
├─ state/
│  └─ seen_urls.json           # 処理済み記事URLの履歴（重複処理防止用）
├─ docs/                       # GitHub Pages 公開ルート
│  ├─ index.html               # ダッシュボード画面
│  ├─ styles.css               # ダークモード基調のレスポンシブスタイル
│  ├─ app.js                   # カテゴリ絞り込み・価格/割引率ソート・検索UIロジック
│  └─ data/
│     └─ sales.json            # フロントエンドが読み込むセール製品データ正本（初期サンプルデータ同梱）
└─ .github/
   └─ workflows/
      └─ daily_update.yml      # 毎日JST 9:00（UTC 0:00）実行＋GitHub Pagesデプロイ
```

---

## 4. Google AI Studio での `GEMINI_API_KEY` 取得手順

1. [Google AI Studio (API Keys)](https://aistudio.google.com/apikey) にアクセスし、Googleアカウントでログインします。
2. **「Create API key」**（APIキーを作成）ボタンをクリックします。
3. 既存のGoogle Cloudプロジェクトを選択するか、新規プロジェクトを作成してキーを発行します。
4. 生成されたAPIキー文字列をコピーし、安全な場所に控えておきます（公開リポジトリのコード内には絶対に直接書き込まないでください）。

---

## 5. ローカルでの動作確認コマンド

### ① フロントエンド画面（ダッシュボード）の確認
`docs/data/sales.json` には初期サンプルデータ（SHANLING AE10 / HW600 / EH2、iBasso Audio D16 TAIPAN / Nunchaku、定番イヤーピース等）が同梱されているため、Python標準の簡易HTTPサーバーですぐに画面を確認できます。

```powershell
# プロジェクトルートで実行
python -m http.server 8000 --directory docs
```

ブラウザで `http://localhost:8000` を開くと、カテゴリタブ切り替え・割引率/価格ソート・フリーワード検索・`NEW`バッジ表示を確認できます。

### ② バッチ処理（RSS取得・Gemini精査）のローカル実行

```powershell
# 1. 仮想環境の作成と有効化
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 依存パッケージのインストール
pip install -r requirements.txt

# 3. 環境変数ファイルの作成
Copy-Item .env.example .env
# .env を開き、GEMINI_API_KEY=取得したAPIキー を設定してください

# 4. RSS取得のみの通信テスト（Gemini APIを消費せずにRSS巡回を確認）
python -m src.main --rss-only

# 5. 本番と同等の精査バッチ実行（docs/data/sales.json と state/seen_urls.json を更新）
python -m src.main
```

---

## 6. GitHub Secrets と GitHub Pages の有効化手順

### Step 1: GitHub Secrets に `GEMINI_API_KEY` を登録
1. GitHubリポジトリページの **Settings** タブを開きます。
2. 左メニューの **Secrets and variables** ＞ **Actions** を選択します。
3. **New repository secret** ボタンをクリックします。
4. 以下を入力して **Add secret** をクリックします：
   - **Name**: `GEMINI_API_KEY`
   - **Secret**: Google AI Studioで取得したAPIキー
5. （任意）使用するモデルを変更したい場合は、**Variables** タブの **New repository variable** から `GEMINI_MODEL`（例: `gemini-2.5-flash` または `gemini-3-flash`）を設定できます。

### Step 2: GitHub Pages のソース設定を有効化
1. リポジトリの **Settings** ＞ 左メニューの **Pages** を開きます。
2. **Build and deployment** セクションの **Source** プルダウンを **「GitHub Actions」** に変更します。

### Step 3: 初回ワークフローの手動実行テスト
1. リポジトリ上部の **Actions** タブを開きます。
2. 左側のワークフロー一覧から **「Daily Audio Sale Update & Deploy Pages」** を選択します。
3. 右側の **Run workflow** ボタン ＞ 緑色の **Run workflow** をクリックして手動実行します。
4. ジョブが正常完了すると、`https://<あなたのGitHubユーザー名>.github.io/<リポジトリ名>/` にダッシュボードが公開され、以降は毎日朝9:00（JST）に自動更新されます。

