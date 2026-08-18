#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信源体检。改 main.py 之前先跑这个 —— 源是死的，后面调多少规则都白搭。

    python check_sources.py
"""

import re
import time
import datetime
from collections import Counter

import requests
import feedparser
from bs4 import BeautifulSoup

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
        print(f"\n[{src['name']}] {src['url']}")

        # 先做一次原始探测：归纳该页面所有链接的 URL 形态。
        # 这样即使 article_re 一条都没匹配上，也能直接看出真实形态该怎么写。
        try:
            r = requests.get(src["url"], timeout=REQ_TIMEOUT, headers={"User-Agent": UA})
            print(f"  HTTP {r.status_code} | {len(r.content)} bytes")
            soup = BeautifulSoup(r.content, "html.parser")

            pats, samples = Counter(), {}
            for a in soup.find_all("a"):
                href = a.get("href") or ""
                text = re.sub(r"\s+", " ", a.get_text() or "").strip()
                if len(text) < 8:
                    continue
                if not (href.startswith("http") or href.startswith("/")):
                    continue
                pat = re.sub(r"\d+", "N", href)      # 数字统一替换成 N
                pats[pat] += 1
                samples.setdefault(pat, (text, href))

            print(f"  URL 形态 TOP6（共 {len(pats)} 种）：")
            for pat, cnt in pats.most_common(6):
                text, href = samples[pat]
                hit = "✓匹配" if src.get("article_re") and src["article_re"].search(href) else "  未匹配"
                print(f"    [{cnt:3}] {hit}  {pat}")
                print(f"          {text[:44]}")
                print(f"          {href}")
        except Exception as ex:
            print(f"  ✗ 原始探测失败: {type(ex).__name__} — {ex}")

        # 再跑一次正式抓取，看实际入池数
        items = fetch_html_links(src, limit=200)
        if not items:
            print(f"  ✗ 正式抓取 0 条 —— 参照上面的形态统计修改 article_re")
        else:
            print(f"  ✓ 正式抓取 {len(items)} 条：")
            for it in items[:6]:
                print(f"    └ {it['title'][:46]}")

    print("\n提示：任何一行是 ✗ 或 ⚠，先修源再谈过滤规则。")


if __name__ == "__main__":
    main()
