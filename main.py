import os
import re
import datetime
import requests
import feedparser

# ======================
# 官方可直接访问 RSS
# ======================

CHINA_FEEDS = [
    # 36氪 官方 RSS
    "https://36kr.com/feed",

    # 创业邦 RSS
    "https://www.cyzone.cn/rss",

    # 猎云网 RSS
    "https://www.lieyunwang.com/rss",

]

OVERSEAS_FEEDS = [
    "https://techcrunch.com/tag/funding/feed/",
]

ACTION_WORDS = ["完成", "获", "获得", "宣布完成", "宣布获得"]
ROUND_WORDS = ["融资", "天使轮", "A轮", "B轮", "C轮", "D轮", "Pre-A", "战略融资", "并购"]
NOISE_WORDS = ["论坛", "峰会", "活动", "会议", "报告", "白皮书", "观点", "盘点", "预测"]

SECTOR_RULES = {
    "AI": ["AI", "人工智能", "大模型", "机器人"],
    "医疗/生物": ["医疗", "医药", "生物", "基因"],
    "硬科技": ["芯片", "半导体", "材料", "制造"],
    "前沿科技": ["量子", "脑机", "核聚变"],
    "消费": ["新消费", "品牌", "零售", "餐饮"],
}

def clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()

def has_any(text, keys):
    text = (text or "").lower()
    return any(k.lower() in text for k in keys)

def detect_sector(text):
    for sector, keys in SECTOR_RULES.items():
        if has_any(text, keys):
            return sector
    return "其他"

def parse_feed(url):
    items = []
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        for e in feed.entries[:50]:
            items.append({
                "title": clean(getattr(e, "title", "")),
                "link": getattr(e, "link", ""),
                "summary": clean(getattr(e, "summary", ""))[:200]
            })
    except Exception as ex:
        items.append({
            "title": f"抓取失败：{url}",
            "link": url,
            "summary": str(ex)
        })
    return items

def is_true_deal(title):
    if has_any(title, NOISE_WORDS):
        return False
    return has_any(title, ACTION_WORDS) and has_any(title, ROUND_WORDS)

def main():
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

    try:
        deals = []

        for url in CHINA_FEEDS:
            items = parse_feed(url)
            for item in items:
                title = item["title"]
                blob = title + " " + item["summary"]
                if is_true_deal(title):
                    sector = detect_sector(blob)
                    if sector != "其他":
                        deals.append({
                            "title": title,
                            "link": item["link"],
                            "sector": sector
                        })

        deals = list({d["title"]: d for d in deals}.values())[:15]

        md = []
        md.append(f"# {today} VC/PE 真融资晨报\n")

        if deals:
            for i, d in enumerate(deals, 1):
                md.append(f"{i}. **[{d['title']}]({d['link']})**")
                md.append(f"   - 赛道：{d['sector']}")
        else:
            md.append("- 今日未筛到明确完成融资标题")

        sendkey = os.environ["SENDKEY"]
        api = f"https://sctapi.ftqq.com/{sendkey}.send"

        requests.post(api, data={
            "title": f"{today} VC/PE 晨报",
            "desp": "\n".join(md)
        }, timeout=20)

    except Exception as ex:
        sendkey = os.environ["SENDKEY"]
        api = f"https://sctapi.ftqq.com/{sendkey}.send"

        requests.post(api, data={
            "title": f"{today} 晨报失败",
            "desp": str(ex)
        }, timeout=20)


if __name__ == "__main__":
    main()
