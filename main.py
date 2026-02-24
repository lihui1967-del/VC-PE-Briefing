import os, re, datetime
import requests
import feedparser

RSS_FEEDS = [
    # 你可以后续在这里继续加RSS链接（每行一个）
    "https://rsshub.app/36kr/newsflashes",   # 36氪快讯（通过RSSHub）
]

KEYWORDS = [
    "AI","人工智能","大模型","AIGC","具身","机器人",
    "医疗","医药","生物","器械","IVD","基因","制药",
    "硬科技","芯片","半导体","材料","光电","量子",
    "消费","新消费","品牌","零售","连锁"
]

def hit(text: str) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in KEYWORDS)

def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()

def fetch_items(max_per_feed=12):
    items = []
    for url in RSS_FEEDS:
        d = feedparser.parse(url)
        for e in d.entries[:max_per_feed]:
            title = clean(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            summary = clean(getattr(e, "summary", ""))[:160]
            blob = f"{title} {summary}"
            if hit(blob):
                items.append((title, link, summary))
    # 去重（按标题+链接）
    seen = set()
    uniq = []
    for t,l,s in items:
        key = (t,l)
        if key not in seen:
            seen.add(key)
            uniq.append((t,l,s))
    return uniq[:25]

def post_to_serverchan(title: str, desp_md: str):
    sendkey = os.environ["SENDKEY"]
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    r = requests.post(url, data={"title": title, "desp": desp_md}, timeout=20)
    r.raise_for_status()

def main():
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    items = fetch_items()

    if not items:
        md = f"## {today} 投融资晨报\n\n今天在订阅源里未筛到匹配关键词的条目（可在后续添加更多RSS源）。"
    else:
        lines = [f"## {today} 投融资晨报（中国为主）", "", "### 🔎 今日命中（关键词筛选）"]
        for i,(t,l,s) in enumerate(items, 1):
            lines.append(f"{i}. **{t}**\n   - {l}\n   - {s}")
        lines.append("")
        lines.append("### 🎯 明日关注清单（自动生成）")
        for t,l,_ in items[:5]:
            lines.append(f"- {t}（继续跟踪后续披露/投资方）\n  {l}")
        md = "\n".join(lines)

    post_to_serverchan(f"{today} VC/PE & 融资晨报", md)

if __name__ == "__main__":
    main()
