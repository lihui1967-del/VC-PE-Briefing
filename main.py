import os
import re
import datetime
import requests
import feedparser

# ======================
#  数据源（先用稳定源）
# ======================

CHINA_FEEDS = [
    "https://rsshub.app/36kr/investment",
    "https://rsshub.app/36kr/newsflashes",
    "https://rsshub.app/cyzone/label/投融资",
]

OVERSEAS_FEEDS = [
    "https://techcrunch.com/tag/funding/feed/",
]

# ======================
#  强过滤规则（B策略）
# ======================

ACTION_WORDS = ["完成", "获", "获得", "宣布完成", "宣布获得"]
ROUND_WORDS = ["融资", "天使轮", "A轮", "B轮", "C轮", "D轮", "Pre-A", "战略融资", "并购"]
NOISE_WORDS = ["论坛", "峰会", "活动", "会议", "报告", "白皮书", "观点", "盘点", "预测"]

FUND_WORDS = ["募资", "募集", "首关", "终关", "设立", "基金", "备案"]

SECTOR_RULES = {
    "AI": ["AI", "人工智能", "大模型", "机器人"],
    "医疗/生物": ["医疗", "医药", "生物", "基因"],
    "硬科技": ["芯片", "半导体", "材料", "制造"],
    "前沿科技": ["量子", "脑机", "核聚变"],
    "消费": ["新消费", "品牌", "零售", "餐饮"],
}

AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:亿|万)?\s*(?:人民币|美元|美金|元)?)")

# ======================
#  工具函数
# ======================

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

def extract_amount(text):
    m = AMOUNT_RE.search(text or "")
    return m.group(1) if m else "未披露"

def parse_feed(url):
    items = []
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        for e in feed.entries[:40]:
            items.append({
                "title": clean(getattr(e, "title", "")),
                "link": getattr(e, "link", ""),
                "summary": clean(getattr(e, "summary", ""))[:200]
            })
    except Exception as ex:
        items.append({
            "title": f"RSS抓取失败：{url}",
            "link": url,
            "summary": str(ex)
        })
    return items

def is_true_deal(title):
    if has_any(title, NOISE_WORDS):
        return False
    return has_any(title, ACTION_WORDS) and has_any(title, ROUND_WORDS)

def is_true_fund(title):
    if has_any(title, NOISE_WORDS):
        return False
    return has_any(title, FUND_WORDS)

# ======================
#  主逻辑
# ======================

def main():

    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

    try:

        deals = []
        funds = []

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
                            "sector": sector,
                            "amount": extract_amount(blob)
                        })

                elif is_true_fund(title):
                    funds.append({
                        "title": title,
                        "link": item["link"],
                        "amount": extract_amount(blob)
                    })

        # 去重
        deals = list({d["title"]: d for d in deals}.values())[:15]
        funds = list({f["title"]: f for f in funds}.values())[:8]

        # 组装 Markdown
        md = []
        md.append(f"# {today} VC/PE 融资晨报（B策略）\n")

        md.append("## 🇨🇳 中国真融资\n")
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(f"{i}. **[{d['title']}]({d['link']})**")
                md.append(f"   - 赛道：{d['sector']}｜金额：{d['amount']}")
        else:
            md.append("- 今日未抓到明确完成融资标题\n")

        md.append("\n## 🏦 基金动态\n")
        if funds:
            for i, f in enumerate(funds, 1):
                md.append(f"{i}. **[{f['title']}]({f['link']})**")
                md.append(f"   - 规模线索：{f['amount']}")
        else:
            md.append("- 今日未抓到明确募资标题\n")

        md.append("\n## 🌍 海外对比\n")

        overseas = []
        for url in OVERSEAS_FEEDS:
            overseas.extend(parse_feed(url))

        for o in overseas[:5]:
            md.append(f"- **[{o['title']}]({o['link']})**")

        md_text = "\n".join(md)

        sendkey = os.environ["SENDKEY"]
        api = f"https://sctapi.ftqq.com/{sendkey}.send"

        requests.post(api, data={
            "title": f"{today} VC/PE 晨报",
            "desp": md_text
        }, timeout=20)

    except Exception as ex:

        sendkey = os.environ["SENDKEY"]
        api = f"https://sctapi.ftqq.com/{sendkey}.send"

        requests.post(api, data={
            "title": f"{today} 晨报生成失败",
            "desp": str(ex)
        }, timeout=20)


if __name__ == "__main__":
    main()
