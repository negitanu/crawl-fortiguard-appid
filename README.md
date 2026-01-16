# FortiGuard App Control Crawler

FortiGuard App Control のアプリケーション ID、名前、説明、デフォルトポートをクロールするツールです。

## 技術スタック

![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-0.9+-FFD43B?style=flat-square&logo=python&logoColor=3776AB)
![requests](https://img.shields.io/badge/requests-2.31+-green?style=flat-square)
![BeautifulSoup4](https://img.shields.io/badge/BeautifulSoup4-4.12+-blue?style=flat-square)
![lxml](https://img.shields.io/badge/lxml-4.9+-orange?style=flat-square)
![rich](https://img.shields.io/badge/rich-13.0+-purple?style=flat-square)

## 機能

- アプリケーション ID の一括取得
- アプリケーション名の取得
- 説明（Description）の取得
- デフォルトポート（Default Ports）の取得
- 進捗状況の可視化（rich ライブラリを使用）
- モジュールとしての利用が可能

## セットアップ

### 前提条件

- Python 3.8 以上
- [uv](https://github.com/astral-sh/uv) がインストールされていること

### インストール

1. 仮想環境を作成:

```bash
uv venv
```

2. 依存関係をインストール:

```bash
uv pip install -r requirements.txt
```

または、仮想環境をアクティベートしてから:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## 実行方法

### 方法 1: uv を使用（推奨）

```bash
uv run python main.py
```

### 方法 2: 仮想環境をアクティベートして実行

```bash
source .venv/bin/activate
python main.py
```

## 出力

実行後、`appid.csv` ファイルが生成されます。このファイルには以下の情報が含まれています：

- **App ID**: アプリケーション ID
- **App Name**: アプリケーション名
- **Description**: アプリケーションの説明
- **Default Ports**: デフォルトポート（カンマ区切り）

## モジュールとしての使用

このツールは Python モジュールとしても使用できます。

### 基本的な使用例

```python
from main import scrape_all

# デフォルト設定で全データをスクレイピング
data = scrape_all()
print(f"取得したデータ数: {len(data)}")
```

### カスタム設定で使用

```python
from main import Config, scrape_all

# カスタム設定を作成
config = Config(
    retry_delay=0.5,              # スリープ時間を0.5秒に設定
    output_file='custom.csv',     # 出力ファイル名を変更
    show_progress=True,           # 進捗バーを表示
    max_workers=10                # 並列実行数を10に設定
)

# スクレイピング実行
data = scrape_all(config)
```

### 進捗バーなしで使用

```python
from main import Config, scrape_all

config = Config(show_progress=False)
data = scrape_all(config)
```

### 個別の関数を使用

```python
from main import Config, create_session, get_total_appids_and_per_page, scrape_page

config = Config()
session = create_session()

# 初期情報を取得
total_appids, items_per_page = get_total_appids_and_per_page(session, config)

# 特定のページをスクレイピング
page_data = scrape_page(session, 1, items_per_page, config)
```

### 設定オプション

`Config`クラスで以下の設定が可能です：

- `base_url` (str): ベース URL（デフォルト: `'https://www.fortiguard.com/appcontrol'`）
- `user_agent` (str): User-Agent 文字列
- `request_timeout` (int): リクエストタイムアウト（秒、デフォルト: `10`）
- `retry_delay` (float): リトライ間の待機時間（秒、デフォルト: `1.0`）
- `max_retries` (int): 最大リトライ回数（デフォルト: `5`）
- `output_file` (str): 出力ファイル名（デフォルト: `'appid.csv'`）
- `show_progress` (bool): 進捗バーの表示/非表示（デフォルト: `True`）
- `max_workers` (int): 並列実行数（デフォルト: `3`）

### より詳細な使用例

`example_usage.py` に追加の使用例があります。参考にしてください。

## 並列処理

このツールは並列処理に対応しており、複数のページやアプリケーション詳細を同時に取得できます。

- **並列数の設定**: `Config`クラスの`max_workers`パラメータで並列実行数を変更できます（デフォルト: `5`）
- **並列化の対象**:
  - ページのスクレイピング（複数ページを並列で取得）
  - アプリケーション詳細の取得（複数のアプリを並列で取得）
- **パフォーマンス**: 並列数を増やすことで処理速度が向上しますが、サーバーへの負荷も増加します

```python
# 並列数を10に設定
config = Config(max_workers=10)
data = scrape_all(config)
```

## 注意事項

- 全アプリケーション（約 6,500 件以上）をスクレイピングするには時間がかかります
- サーバーへの負荷を考慮し、`retry_delay`と`max_workers`を適切に設定してください
- デフォルトポートの取得には各アプリケーションの詳細ページへのアクセスが必要なため、処理時間が長くなります
- 並列数を大きくしすぎると、サーバー側でレート制限やブロックが発生する可能性があります