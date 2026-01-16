"""
モジュールとして使用する例
"""
from main import Config, scrape_all, scrape_page, create_session, get_total_appids_and_per_page, save_to_csv

# 例1: デフォルト設定でスクレイピング
def example1():
    """デフォルト設定で全データをスクレイピング"""
    data = scrape_all()
    print(f"取得したデータ数: {len(data)}")
    return data


# 例2: カスタム設定でスクレイピング
def example2():
    """カスタム設定でスクレイピング"""
    config = Config(
        retry_delay=0.5,  # スリープ時間を0.5秒に設定
        output_file='custom_output.csv',  # 出力ファイル名を変更
        show_progress=True  # 進捗バーを表示
    )
    data = scrape_all(config)
    return data


# 例3: 進捗バーなしでスクレイピング
def example3():
    """進捗バーを表示せずにスクレイピング"""
    config = Config(show_progress=False)
    data = scrape_all(config)
    return data


# 例4: 個別の関数を使用
def example4():
    """個別の関数を使用してカスタム処理"""
    config = Config(retry_delay=0.1)
    session = create_session()
    
    # 初期情報を取得
    total_appids, items_per_page = get_total_appids_and_per_page(session, config)
    print(f"総数: {total_appids}, 1ページあたり: {items_per_page}")
    
    # 最初のページだけ取得
    page_data = scrape_page(session, 1, items_per_page, config)
    print(f"1ページ目のデータ数: {len(page_data)}")
    
    return page_data


# 例5: データを取得してカスタム処理
def example5():
    """データを取得してCSV以外の形式で保存"""
    data = scrape_all()
    
    # JSON形式で保存する例
    import json
    json_data = [
        {
            'app_id': item[0],
            'app_name': item[1],
            'description': item[2],
            'default_ports': item[3]
        }
        for item in data
    ]
    
    with open('appid.json', 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    
    print(f"JSON形式で {len(json_data)} 件を保存しました")


if __name__ == '__main__':
    # 実行例（コメントアウトを解除して実行）
    # example1()
    # example2()
    # example3()
    # example4()
    # example5()
    pass
