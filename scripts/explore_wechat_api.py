import os
import json
from datetime import date, timedelta
from dotenv import load_dotenv

from wechat_smoke_test import _accounts, _token, _post

def print_result(name, data):
    print(f"\n{'='*40}")
    print(f"API Endpoint: {name}")
    print(f"{'='*40}")
    
    # We might get a list or dict. Let's pretty print it, but truncate long lists.
    if isinstance(data, dict) and "list" in data:
        items = data["list"]
        print(f"Returned {len(items)} items. Showing up to 2 items:")
        print(json.dumps(items[:2], indent=2, ensure_ascii=False))
        if len(items) > 2:
            print("... (truncated)")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))

def main():
    accounts = _accounts()
    if not accounts:
        print("No accounts found in .env")
        return
    
    account = accounts[0] # Just use the first one
    print(f"Using account: {account['name']}")
    
    token = _token(account["app_id"], account["app_secret"])
    
    # Usually we test yesterday's data
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # Test up to 3 days ago for things that might have wider ranges
    three_days_ago = (date.today() - timedelta(days=3)).isoformat()
    
    # 1. User Read (Total account traffic for the day)
    try:
        user_read = _post("datacube/getuserread", token, {
            "begin_date": three_days_ago, 
            "end_date": yesterday
        })
        print_result("datacube/getuserread (3天内所有图文宏观流量)", user_read)
    except Exception as e:
        print(f"getuserread error: {e}")

    # 2. User Summary (User growth)
    try:
        user_summary = _post("datacube/getusersummary", token, {
            "begin_date": three_days_ago, 
            "end_date": yesterday
        })
        print_result("datacube/getusersummary (3天内用户增长情况)", user_summary)
    except Exception as e:
        print(f"getusersummary error: {e}")

    # 3. Article Summary (Articles published on that day)
    try:
        article_summary = _post("datacube/getarticlesummary", token, {
            "begin_date": yesterday, 
            "end_date": yesterday
        })
        print_result("datacube/getarticlesummary (昨日群发文章流量)", article_summary)
    except Exception as e:
        print(f"getarticlesummary error: {e}")

    # 4. Article Total (Articles published on that day, traffic over up to 7 days)
    # We will query an article published 3 days ago, and see its traffic
    try:
        article_total = _post("datacube/getarticletotal", token, {
            "begin_date": three_days_ago, 
            "end_date": three_days_ago
        })
        print_result("datacube/getarticletotal (3天前群发文章累积流量)", article_total)
    except Exception as e:
        print(f"getarticletotal error: {e}")

if __name__ == '__main__':
    main()
