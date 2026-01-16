import csv
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from bs4.element import Tag
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


@dataclass
class Config:
    """スクレイピング設定を管理するクラス。"""
    base_url: str = 'https://www.fortiguard.com/appcontrol'
    user_agent: str = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
    request_timeout: int = 10
    retry_delay: float = 2.0  # リトライ間の待機時間（秒）
    max_retries: int = 5
    output_file: str = 'appid.csv'
    show_progress: bool = True
    max_workers: int = 1  # 並列実行数（サーバー負荷軽減のため控えめに）
    
    @property
    def headers(self) -> Dict[str, str]:
        """HTTPヘッダーを返す。"""
        return {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
        }


# デフォルト設定インスタンス
_default_config = Config()

# 公開API
__all__ = [
    'Config',
    'scrape_all',
    'scrape_page',
    'scrape_all_pages',
    'get_total_appids_and_per_page',
    'get_app_details',
    'extract_app_data',
    'create_session',
    'save_to_csv',
    'calculate_total_pages',
    'main',
]


def create_session(
    pool_connections: int = 10,
    pool_maxsize: int = 10,
    max_retries: int = 3,
    backoff_factor: float = 0.5,
) -> requests.Session:
    """
    HTTPコネクションプーリングとリトライ戦略が設定されたrequestsセッションを作成して返す。

    Args:
        pool_connections: コネクションプールの数
        pool_maxsize: プールあたりの最大接続数
        max_retries: 接続エラー時の最大リトライ回数
        backoff_factor: リトライ間の待機時間係数（指数バックオフ）

    Returns:
        設定済みのrequestsセッション
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=retry_strategy,
    )
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


def fetch_page(
    session: requests.Session,
    url: str,
    config: Config = None,
    max_retries: Optional[int] = None
) -> Optional[BeautifulSoup]:
    """
    ページを取得してBeautifulSoupオブジェクトを返す。

    Args:
        session: requestsセッションオブジェクト
        url: 取得するURL
        config: 設定オブジェクト（省略時はデフォルト設定を使用）
        max_retries: 最大リトライ回数（省略時はconfigの値を使用）

    Returns:
        BeautifulSoupオブジェクト。すべてのリトライが失敗した場合はNone
    """
    if config is None:
        config = _default_config
    if max_retries is None:
        max_retries = config.max_retries

    for attempt in range(max_retries):
        try:
            # リクエスト前に2秒待機
            time.sleep(1.0)
            response = session.get(url, headers=config.headers, timeout=config.request_timeout)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'lxml')
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            # SSL/接続エラーは指数バックオフでリトライ
            delay = config.retry_delay * (2 ** attempt)  # 指数バックオフ
            logger.warning("Connection error fetching %s (attempt %d/%d): %s. Retrying in %.1fs...",
                          url, attempt + 1, max_retries, e, delay)
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                return None
        except requests.exceptions.RequestException as e:
            logger.warning("Error fetching %s (attempt %d/%d): %s", url, attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(config.retry_delay)
            else:
                return None
    return None


def get_total_appids_and_per_page(session: requests.Session, config: Config = None) -> Tuple[int, int]:
    """
    最初のページからアプリIDの総数と1ページあたりのアイテム数を取得する。
    
    Args:
        session: requestsセッションオブジェクト
        config: 設定オブジェクト（省略時はデフォルト設定を使用）
        
    Returns:
        (total_appids, items_per_page) のタプル
    """
    if config is None:
        config = _default_config
    soup = fetch_page(session, config.base_url, config)
    if soup is None:
        raise RuntimeError("Failed to fetch initial page")
    
    # Find total count from <p class="m-2">Total: <b>6,556</b></p>
    total_element = soup.find('p', class_='m-2')
    if total_element is None:
        raise ValueError("Could not find total count element")
    
    total_text = total_element.get_text()
    # Extract number from "Total: 6,556" or similar
    total_match = re.search(r'Total:\s*<b>([\d,]+)</b>', str(total_element))
    if not total_match:
        # Try without HTML tags
        total_match = re.search(r'Total:\s*([\d,]+)', total_text)
    
    if not total_match:
        raise ValueError(f"Could not extract total count from: {total_text}")
    
    total_appids = int(total_match.group(1).replace(',', ''))
    
    # Find app list items - they are div.row elements with onclick containing /appcontrol/
    app_rows = soup.find_all('div', class_='row', onclick=re.compile(r'/appcontrol/\d+'))
    items_per_page = len(app_rows)
    
    if items_per_page == 0:
        raise ValueError("Could not find any application items on the page")
    
    return total_appids, items_per_page


def calculate_total_pages(total_appids: int, items_per_page: int) -> int:
    """
    必要な総ページ数を計算する。

    Args:
        total_appids: アプリIDの総数
        items_per_page: 1ページあたりのアイテム数

    Returns:
        総ページ数
    """
    if items_per_page == 0:
        raise ValueError("Items per page cannot be zero")
    return math.ceil(total_appids / items_per_page)


def extract_rating_count(elem: Tag) -> int:
    """
    レーティング（星や円）の数をカウントする。
    
    Args:
        elem: レーティング要素を含むBeautifulSoup要素
        
    Returns:
        レーティング数（1-5）
    """
    if elem is None:
        return 0
    # black-background-star-icon, black-background-circle-icon などのalt属性を持つ要素をカウント
    dark_items = elem.find_all('img', alt=re.compile(r'black-background'))
    return len(dark_items)


def extract_app_data(app_element: Tag) -> Optional[Tuple[int, str, str, str, int, int]]:
    """
    アプリ要素からアプリID、名前、説明、カテゴリ、リスク、人気度を抽出する。
    
    Args:
        app_element: アプリ情報を含むBeautifulSoup要素（onclick属性を持つdiv.row）
        
    Returns:
        (app_id, app_name, description, category, risk, popularity) のタプル。抽出に失敗した場合はNone
    """
    try:
        # Extract app ID from onclick attribute: onclick="location.href = '/appcontrol/59958'"
        onclick = app_element.get('onclick', '')
        app_id_match = re.search(r'/appcontrol/(\d+)', onclick)
        if not app_id_match:
            return None
        
        app_id = int(app_id_match.group(1))
        
        # Extract app name from <div class="col-md-3"><b>App Name</b></div>
        name_col = app_element.find('div', class_='col-md-3', style=re.compile(r'word-break'))
        if name_col is None:
            return None
        
        name_bold = name_col.find('b')
        if name_bold is None:
            return None
        
        app_name_full = name_bold.get_text().strip()
        
        # Extract category from app name (e.g., "DNF (Update)" -> "Update")
        category = ''
        category_match = re.search(r'\(([^)]+)\)$', app_name_full)
        if category_match:
            category = category_match.group(1)
            # Remove category from app name
            app_name = re.sub(r'\s*\([^)]+\)$', '', app_name_full)
        else:
            app_name = app_name_full
        
        # Extract description from <div class="col-md-3"><small>Description...</small></div>
        # Column 2 contains the description
        cols = app_element.find_all('div', class_='col-md-3')
        description = ''
        if len(cols) >= 2:
            desc_col = cols[1]  # Second column
            desc_small = desc_col.find('small')
            if desc_small:
                description = desc_small.get_text().strip()
        
        # Extract Risk and Popularity from columns 3 and 4
        all_cols = app_element.find_all('div', class_=True)
        risk = 0
        popularity = 0
        
        # Column 3 (col-md-2) contains Risk
        if len(all_cols) >= 3:
            risk_col = all_cols[2]
            risk = extract_rating_count(risk_col)
        
        # Column 4 (col-md-2) contains Popularity
        if len(all_cols) >= 4:
            popularity_col = all_cols[3]
            popularity = extract_rating_count(popularity_col)
        
        return (app_id, app_name, description, category, risk, popularity)
    except (AttributeError, ValueError, IndexError) as e:
        logger.debug("Error extracting app data: %s", e)
        return None


def get_app_details(session: requests.Session, app_id: int, config: Config = None) -> Dict[str, str]:
    """
    アプリケーションの詳細ページから追加情報を取得する。
    
    Args:
        session: requestsセッションオブジェクト
        app_id: アプリケーションID
        config: 設定オブジェクト（省略時はデフォルト設定を使用）
        
    Returns:
        詳細情報の辞書。キー: default_ports, affected_products, impact, technology, behavior, references
    """
    if config is None:
        config = _default_config
    
    details = {
        'default_ports': '',
        'affected_products': '',
        'impact': '',
        'technology': '',
        'behavior': '',
        'references': ''
    }
    
    try:
        url = f"{config.base_url}/{app_id}"
        soup = fetch_page(session, url, config)
        
        if soup is None:
            return details
        
        # Find all <div class="detail-item"> elements
        detail_items = soup.find_all('div', class_='detail-item')
        for item in detail_items:
            h3 = item.find('h3')
            if h3 is None:
                continue
            
            title = h3.get_text().strip()
            
            # Extract content based on title
            if 'Default Ports' in title:
                ul = item.find('ul')
                if ul:
                    ports = []
                    for li in ul.find_all('li'):
                        port_text = li.get_text().strip()
                        if port_text:
                            ports.append(port_text)
                    details['default_ports'] = ', '.join(ports)
            
            elif 'Affected Products' in title:
                p = item.find('p')
                if p:
                    details['affected_products'] = p.get_text().strip()
                else:
                    # Sometimes it's in a list
                    ul = item.find('ul')
                    if ul:
                        products = []
                        for li in ul.find_all('li'):
                            product_text = li.get_text().strip()
                            if product_text:
                                products.append(product_text)
                        details['affected_products'] = ', '.join(products)
            
            elif 'Impact' in title:
                p = item.find('p')
                if p:
                    details['impact'] = p.get_text().strip()
            
            elif 'Technology' in title:
                p = item.find('p')
                if p:
                    details['technology'] = p.get_text().strip()
            
            elif 'Behavior' in title:
                ul = item.find('ul')
                if ul:
                    behaviors = []
                    for li in ul.find_all('li'):
                        behavior_text = li.get_text().strip()
                        if behavior_text:
                            behaviors.append(behavior_text)
                    details['behavior'] = ', '.join(behaviors)
                else:
                    p = item.find('p')
                    if p:
                        details['behavior'] = p.get_text().strip()
            
            elif 'References' in title:
                ul = item.find('ul')
                if ul:
                    refs = []
                    for li in ul.find_all('li'):
                        a = li.find('a')
                        if a:
                            ref_text = a.get('href', '').strip()
                            if ref_text:
                                refs.append(ref_text)
                        else:
                            ref_text = li.get_text().strip()
                            if ref_text:
                                refs.append(ref_text)
                    details['references'] = ', '.join(refs)
        
        return details
    except Exception as e:
        logger.warning("Error fetching app details for app %d: %s", app_id, e)
        return details




def scrape_page(session: requests.Session, page_num: int, expected_items: int, config: Config = None) -> List[Tuple[int, str, str, str, int, int]]:
    """
    単一ページをスクレイピングしてアプリデータのリストを返す。
    
    Args:
        session: requestsセッションオブジェクト
        page_num: スクレイピングするページ番号
        expected_items: 1ページあたりの期待されるアイテム数
        config: 設定オブジェクト（省略時はデフォルト設定を使用）
        
    Returns:
        (app_id, app_name, description, category, risk, popularity) を含むタプルのリスト
    """
    if config is None:
        config = _default_config
    if page_num == 1:
        url = config.base_url
    else:
        url = f"{config.base_url}?category=&popularity=&risk=&page={page_num}"
    
    # アイテム数が0の場合にリトライ
    for attempt in range(config.max_retries):
        soup = fetch_page(session, url, config)
        
        if soup is None:
            if attempt < config.max_retries - 1:
                logger.warning("Failed to fetch page %d (attempt %d/%d), retrying...", page_num, attempt + 1, config.max_retries)
                time.sleep(config.retry_delay)
                continue
            return []
        
        # Find app list items - they are div.row elements with onclick containing /appcontrol/
        app_elements = soup.find_all('div', class_='row', onclick=re.compile(r'/appcontrol/\d+'))
        
        # アイテム数が0の場合、リトライ
        if len(app_elements) == 0:
            if attempt < config.max_retries - 1:
                logger.warning("Page %d returned 0 items (attempt %d/%d), retrying...", page_num, attempt + 1, config.max_retries)
                time.sleep(config.retry_delay)
                continue
            else:
                logger.warning("Page %d returned 0 items after %d attempts", page_num, config.max_retries)
                return []
        
        # Validate page completeness (except for last page)
        if len(app_elements) != expected_items and page_num > 1:
            logger.warning("Page %d has %d items, expected %d", page_num, len(app_elements), expected_items)
        
        app_data = []
        for app_element in app_elements:
            data = extract_app_data(app_element)
            if data is not None:
                app_data.append(data)
        
        return app_data
    
    return []


def _scrape_page_wrapper(args: Tuple[int, int, Config]) -> Tuple[int, List[Tuple[int, str, str, str, int, int]]]:
    """
    ページスクレイピングのラッパー関数（並列処理用）。
    
    Args:
        args: (page_num, items_per_page, config) のタプル
        
    Returns:
        (page_num, page_data) のタプル。page_dataは(app_id, app_name, description, category, risk, popularity)のリスト
    """
    page_num, items_per_page, config = args
    session = create_session()  # 各スレッドで独自のセッションを作成
    try:
        page_data = scrape_page(session, page_num, items_per_page, config)
        return (page_num, page_data)
    finally:
        session.close()


def _get_details_wrapper(args: Tuple[int, str, str, str, int, int, Config]) -> Tuple[int, str, str, str, int, int, Dict[str, str]]:
    """
    アプリケーション詳細取得のラッパー関数（並列処理用）。
    
    Args:
        args: (app_id, app_name, description, category, risk, popularity, config) のタプル
        
    Returns:
        (app_id, app_name, description, category, risk, popularity, details_dict) のタプル
    """
    app_id, app_name, description, category, risk, popularity, config = args
    session = create_session()  # 各スレッドで独自のセッションを作成
    try:
        details = get_app_details(session, app_id, config)
        return (app_id, app_name, description, category, risk, popularity, details)
    finally:
        session.close()


def scrape_all_pages(
    total_pages: int,
    items_per_page: int,
    config: Config = None
) -> List[Dict[str, Any]]:
    """
    すべてのページをスクレイピングしてアプリデータを収集する。

    Args:
        total_pages: スクレイピングする総ページ数
        items_per_page: 1ページあたりの期待されるアイテム数
        config: 設定オブジェクト（省略時はデフォルト設定を使用）

    Returns:
        アプリケーション情報の辞書のリスト。各辞書には以下のキーが含まれる:
        app_id, app_name, description, category, risk, popularity,
        default_ports, affected_products, impact, technology, behavior, references
    """
    if config is None:
        config = _default_config
    console = Console()
    
    # 総アイテム数を計算（概算）
    total_items = total_pages * items_per_page
    
    # 進捗バーのコンテキストマネージャー
    progress_context = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) if config.show_progress else _NoProgress()
    
    all_data = []
    
    with progress_context as progress:
        # ページスクレイピングのタスク
        page_task = None
        app_task = None
        if config.show_progress:
            page_task = progress.add_task(
                "[cyan]ページをスクレイピング中...",
                total=total_pages
            )
            # アプリケーション詳細取得のタスク
            app_task = progress.add_task(
                "[green]アプリケーション詳細を取得中...",
                total=total_items
            )
        
        # ステップ1: ページを並列でスクレイピング
        page_results = {}  # page_num -> page_data
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            # すべてのページを並列でスクレイピング
            future_to_page = {
                executor.submit(_scrape_page_wrapper, (page_num, items_per_page, config)): page_num
                for page_num in range(1, total_pages + 1)
            }
            
            for future in as_completed(future_to_page):
                page_num = future_to_page[future]
                try:
                    result_page_num, page_data = future.result()
                    page_results[result_page_num] = page_data
                    if config.show_progress and page_task is not None:
                        progress.update(
                            page_task,
                            advance=1,
                            description=f"[cyan]ページ {result_page_num}/{total_pages} をスクレイピング中..."
                        )
                except Exception as e:
                    console.print(f"[red]ページ {page_num} のスクレイピングでエラー: {e}[/red]")
        
        # ステップ2: すべてのアプリケーションの詳細情報を並列で取得
        all_apps = []
        for page_num in sorted(page_results.keys()):
            for app_data in page_results[page_num]:
                # app_data is (app_id, app_name, description, category, risk, popularity)
                all_apps.append(app_data)
        
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            # すべてのアプリケーションの詳細情報を並列で取得
            future_to_app = {
                executor.submit(_get_details_wrapper, (*app_data, config)): app_data[0]
                for app_data in all_apps
            }
            
            for future in as_completed(future_to_app):
                app_id = future_to_app[future]
                try:
                    result = future.result()
                    # result is (app_id, app_name, description, category, risk, popularity, details_dict)
                    app_id, app_name, description, category, risk, popularity, details = result
                    
                    # 辞書形式でデータを構築
                    app_dict = {
                        'app_id': app_id,
                        'app_name': app_name,
                        'description': description,
                        'category': category,
                        'risk': risk,
                        'popularity': popularity,
                        'default_ports': details.get('default_ports', ''),
                        'affected_products': details.get('affected_products', ''),
                        'impact': details.get('impact', ''),
                        'technology': details.get('technology', ''),
                        'behavior': details.get('behavior', ''),
                        'references': details.get('references', '')
                    }
                    all_data.append(app_dict)
                    
                    if config.show_progress and app_task is not None:
                        progress.update(
                            app_task,
                            advance=1,
                            description=f"[green]アプリ {app_id}: {app_name[:30]}..."
                        )
                except Exception as e:
                    console.print(f"[red]アプリ {app_id} の詳細情報取得でエラー: {e}[/red]")
    
    return all_data


class _NoProgress:
    """進捗バーを表示しない場合のダミーコンテキストマネージャー。"""
    def __enter__(self) -> '_NoProgress':
        return self
    def __exit__(self, *args: Any) -> None:
        pass
    def add_task(self, *args: Any, **kwargs: Any) -> None:
        return None
    def update(self, *args: Any, **kwargs: Any) -> None:
        pass


def save_to_csv(data: List[Dict[str, Any]], filename: str) -> None:
    """
    アプリデータをCSVファイルに保存する。
    
    Args:
        data: アプリケーション情報の辞書のリスト
        filename: 出力CSVファイル名
    """
    if not data:
        logger.warning("保存するデータがありません")
        return
    
    # app_idでソート
    sorted_data = sorted(data, key=lambda x: x.get('app_id', 0))
    
    # CSVのヘッダーを定義
    fieldnames = [
        'app_id',
        'app_name',
        'description',
        'category',
        'risk',
        'popularity',
        'default_ports',
        'affected_products',
        'impact',
        'technology',
        'behavior',
        'references'
    ]
    
    with open(filename, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator='\n')
        # ヘッダーを書き込む
        writer.writeheader()
        # データを書き込む
        for row in sorted_data:
            writer.writerow(row)


def scrape_all(
    config: Config = None,
    output_file: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    すべてのアプリケーションIDをスクレイピングして返す。
    
    Args:
        config: 設定オブジェクト（省略時はデフォルト設定を使用）
        output_file: 出力ファイル名（省略時はconfig.output_fileを使用）
        
    Returns:
        アプリケーション情報の辞書のリスト。各辞書には以下のキーが含まれる:
        app_id, app_name, description, category, risk, popularity,
        default_ports, affected_products, impact, technology, behavior, references
    """
    if config is None:
        config = _default_config
    if output_file is None:
        output_file = config.output_file
    
    console = Console()
    session = create_session()
    
    try:
        # 初期情報を取得
        if config.show_progress:
            console.print("[bold cyan]初期情報を取得中...[/bold cyan]")
        total_appids, items_per_page = get_total_appids_and_per_page(session, config)
        if config.show_progress:
            console.print(f"[green]✓[/green] 総アプリID数: [bold]{total_appids}[/bold], 1ページあたりのアイテム数: [bold]{items_per_page}[/bold]")
        
        # 総ページ数を計算
        total_pages = calculate_total_pages(total_appids, items_per_page)
        if config.show_progress:
            console.print(f"[green]✓[/green] スクレイピングする総ページ数: [bold]{total_pages}[/bold]")
            console.print()
        
        # すべてのページをスクレイピング
        all_data = scrape_all_pages(total_pages, items_per_page, config)
        
        if config.show_progress:
            console.print()
            # 結果を報告
            console.print(f"[bold green]✓ 完了:[/bold green] {len(all_data)} / {total_appids} 個のアプリIDを収集しました")
        
        return all_data
        
    except Exception as e:
        if config.show_progress:
            console.print(f"[bold red]エラー:[/bold red] {e}")
        raise


def main(config: Config = None):
    """
    スクレイピング処理を統括するメイン関数。
    
    Args:
        config: 設定オブジェクト（省略時はデフォルト設定を使用）
    """
    if config is None:
        config = _default_config
    
    console = Console()
    
    try:
        # すべてのデータをスクレイピング
        all_data = scrape_all(config)
        
        # CSVに保存
        console.print(f"[cyan]データを {config.output_file} に保存中...[/cyan]")
        save_to_csv(all_data, config.output_file)
        console.print(f"[bold green]✓ データを {config.output_file} に保存しました[/bold green]")
        
    except Exception as e:
        console.print(f"[bold red]エラー:[/bold red] {e}")
        raise


if __name__ == '__main__':
    main()
