#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信源体检。改 main.py 之前先跑这个 —— 源是死的，后面调多少规则都白搭。

    python check_sources.py
"""

import time
import datetime
import requests
import feedparser

from main import (
    CHINA_RSS_FEEDS, OVERSEAS_FEEDS, CHINA_HTML_SOURCES,
    fetch_html_links, UA, REQ_TIMEOUT,
)


def check_rss(name, url):
    try:
        r = requests.get(url, timeout=REQ_TIMEOUT, headers={"User-Agent": UA})
        r.raise_for_status()
        d = feedparser.parse(r.content)
        n = len(d.entries)
        if n == 0:
            return f"✗ {name:12} 解析到 0 条 —— 源已失效或结构变了  <{url}>"

        newest, has_ts = None, 0
        now = time.time()
        for e in d.entries:
            ts = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if ts:
                has_ts += 1
                age = (now - time.mktime(ts)) / 3600
                if newest is None or age < newest:
                    newest = age

        ts_note = f"{has_ts}/{n} 条带时间戳"
        if newest is None:
            return f"⚠ {name:12} {n:3} 条，但无一带时间戳 —— 时间窗过滤会失效  <{url}>"
        if newest > 72:
            return f"⚠ {name:12} {n:3} 条，最新一条已 {newest:.0f}h 前 —— 疑似停更（{ts_note}）"
        return f"✓ {name:12} {n:3} 条，最新 {newest:.1f}h 前（{ts_note}）\n     └ {d.entries[0].get('title','')[:60]}"

    except Exception as ex:
        return f"✗ {name:12} {type(ex).__name__}: {ex}  <{url}>"


def main():
    print(f"信源体检 @ {datetime.datetime.now():%Y-%m-%d %H:%M}\n")

    print("── 中国 RSS " + "─" * 50)
    for name, url in CHINA_RSS_FEEDS:
        print(check_rss(name, url))

    print("\n── 海外 RSS " + "─" * 50)
    for name, url in OVERSEAS_FEEDS:
        print(check_rss(name, url))

    print("\n── 中国 HTML " + "─" * 49)
    for src in CHINA_HTML_SOURCES:
        items = fetch_html_links(src, limit=200)
        if not items:
            print(f"✗ {src['name']:12} 0 条 —— article_re 大概率没匹配上，"
                  f"去页面上抄几个真实 URL 对一下")
        else:
            print(f"✓ {src['name']:12} {len(items):3} 条文章链接")
            for it in items[:5]:
                print(f"     └ {it['title'][:50]}")
                print(f"       {it['link']}")

    print("\n提示：任何一行是 ✗ 或 ⚠，先修源再谈过滤规则。")


if __name__ == "__main__":
    main()
