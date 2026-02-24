import os
import re
import datetime
import requests
import feedparser
from bs4 import BeautifulSoup

# ======================
# 配置区
# ======================

# 海外 RSS（你之前已验证 OK）
OVERSEAS_FEEDS = [
    "https://techcrunch.com/tag/funding/feed/",
    "https://www.fiercebiotech.com/rss/xml",
]

# 中国 RSS：你图里验证 OK 的
CHINA_RSS_FEEDS = [
    "https://36kr.com/feed",          # OK(30)
    "http://www.tmtpost.com/rss.xml", # OK(20)
    "https://cn.technode.com/feed/",  # OK(10)
]

# 中国 HTML：用于“投融资专栏/行业投融资讯”抓取（不走 RSSHub）
CHINA_HTML_SOURCES = [
    # 36kr 投融资频道
    {"name": "36kr-投融资", "url": "https://36kr.com/investment", "base": "https://36kr.com"},
    # 投资界（PEdaily）- 资讯首页（含大量投融资/募资标题）
    {"name": "投资界-资讯", "url": "https://news.pedaily.cn/", "base": "https://news.pedaily.cn"},
]

# B策略：标题含“融资”即算候选（但仍排噪音）
NOISE_WORDS = [
    "论坛", "峰会", "活动", "会议", "报告", "白皮书", "观点", "盘点", "预测", "招聘", "发布会", "圆桌", "直播",
    "训练营", "课程", "研讨会"
]

# 基金/募资强信号（标题级）
FUND_WORDS = ["募资", "募集", "首关", "终关", "设立", "成立", "备案", "基金", "GP", "LP", "FOF"]

# 你关注赛道（用于分组；不命中=仍可收录，但放“其他/待归类”）
SECTOR_RULES = {
    "AI": ["AI", "人工智能", "大模型", "LLM", "AIGC", "多模态", "算力", "机器人", "具身", "自动驾驶"],
    "医疗/生物": ["医疗", "医药", "生物", "器械", "IVD", "基因", "细胞", "抗体", "肿瘤", "诊断", "制药"],
    "硬科技": ["芯片", "半导体", "EDA", "材料", "先进制造", "工业", "传感", "光电", "储能", "电池", "航空航天"],
    "前沿科技": ["量子", "脑机", "BCI", "核聚变", "空间", "卫星", "合成生物", "新材料", "超导"],
    "消费": ["新消费", "品牌", "零售", "连锁", "餐饮", "咖啡", "美妆", "母婴", "潮玩", "宠物", "饮料", "食品"],
}

AMOUNT_RE = re.compile(r"((?:超|近|约)?\s*\d+(?:\.\d+)?\s*(?:亿|万)?\s*(?:人民币|元|美元|美金|US\$|USD|RMB)?)", re.I)


# ======================
# 工具函数
# ======================

def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def has_any(text: str, keys) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keys)

def detect_sector(text: str) -> str:
    for sector, keys in SECTOR_RULES.items():
        if has_any(text, keys):
            return sector
    return "其他/待归类"

def extract_amount(text: str) -> str:
    m = AMOUNT_RE.search((text or "").replace(",", ""))
    return m.group(1).strip() if m else "未披露"

def is_noise(title: str) -> bool:
    return has_any(title, NOISE_WORDS)

def is_true_deal_B(title: str) -> bool:
    """B策略：标题包含“融资”就收（但排除噪音）"""
    if not title:
        return False
    if is_noise(title):
        return False
    return "融资" in title

def is_fund_news(title: str) -> bool:
    if not title:
        return False
    if is_noise(title):
        return False
    return has_any(title, FUND_WORDS)

def fetch_rss(url: str, limit=50):
    """RSS 直连抓取：返回 (items, status)"""
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        d = feedparser.parse(r.content)
        items = []
        for e in d.entries[:limit]:
            items.append({
                "title": clean(getattr(e, "title", "")),
                "link": getattr(e, "link", ""),
                "summary": clean(getattr(e, "summary", ""))[:220],
            })
        return items, f"OK({len(items)})"
    except Exception as ex:
        return [], f"FAIL({type(ex).__name__}) {ex}"

def fetch_html_links(name: str, url: str, base: str, limit=120):
    """HTML 抓取公开页面的文章链接：返回 (links, status)
       links: list[dict(title, link)]
    """
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        out = []
        for a in soup.find_all("a"):
            title = clean(a.get_text() or "")
            href = a.get("href") or ""
            if not title or len(title) < 6:
                continue

            # 统一成绝对链接
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = base.rstrip("/") + href
            elif not href.startswith("http"):
                continue

            out.append({"title": title, "link": href})

        # 去重
        seen = set()
        uniq = []
        for it in out:
            k = (it["title"], it["link"])
            if k not in seen:
                seen.add(k)
                uniq.append(it)

        return uniq[:limit], f"OK({len(uniq[:limit])})"
    except Exception as ex:
        return [], f"FAIL({type(ex).__name__}) {ex}"

def post_to_serverchan(title: str, md: str):
    sendkey = os.environ["SENDKEY"]
    api = f"https://sctapi.ftqq.com/{sendkey}.send"
    r = requests.post(api, data={"title": title, "desp": md}, timeout=25)
    r.raise_for_status()


# ======================
# 主逻辑
# ======================

def main():
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

    try:
        diag_cn, diag_os = [], []
        pool_cn = []

        # 1) 中国 RSS 抓取
        for url in CHINA_RSS_FEEDS:
            items, st = fetch_rss(url, limit=60)
            diag_cn.append(f"- {url} -> {st}")
            for it in items:
                it["_src"] = url
            pool_cn.extend(items)

        # 2) 中国 HTML 抓取（投融资专栏/投融资资讯）
        for src in CHINA_HTML_SOURCES:
            links, st = fetch_html_links(src["name"], src["url"], src["base"], limit=160)
            diag_cn.append(f"- {src['name']} HTML -> {st} ({src['url']})")
            for it in links:
                it["summary"] = it.get("summary", "")
                it["_src"] = src["name"]
            pool_cn.extend(links)

        # 3) 过滤：融资事件 & 基金动态
        deals, funds = [], []
        for it in pool_cn:
            title = it.get("title", "")
            blob = f"{title} {it.get('summary','')}"
            if is_true_deal_B(title):
                deals.append({
                    "title": title,
                    "link": it.get("link", ""),
                    "sector": detect_sector(blob),
                    "amount": extract_amount(blob),
                    "src": it.get("_src", ""),
                })
            elif is_fund_news(title):
                funds.append({
                    "title": title,
                    "link": it.get("link", ""),
                    "amount": extract_amount(blob),
                    "src": it.get("_src", ""),
                })

        # 去重 + 控制数量
        deals = list({d["title"]: d for d in deals}.values())[:20]  # 你要 20 条左右
        funds = list({f["title"]: f for f in funds}.values())[:10]

        # 4) 海外对比
        pool_os = []
        for url in OVERSEAS_FEEDS:
            items, st = fetch_rss(url, limit=30)
            diag_os.append(f"- {url} -> {st}")
            pool_os.extend(items)

        overseas = []
        for it in pool_os:
            blob = (it["title"] + " " + it.get("summary", "")).lower()
            if any(k in blob for k in ["funding", "financing", "raised", "series", "seed", "round"]):
                overseas.append({"title": it["title"], "link": it["link"]})
        overseas = list({o["title"]: o for o in overseas}.values())[:5]

        # 5) 输出
        md = []
        md.append(f"# {today} 股权投融资 Daily Briefing（B策略：标题含“融资”即入池）\n")

        md.append("## ✅ 抓取诊断（中国源）")
        md.extend(diag_cn)
        md.append("")

        md.append("## 🇨🇳 中国融资动态（≤20）")
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(f"{i}. **[{d['title']}]({d['link']})**")
                md.append(f"   - 赛道：{d['sector']}｜金额：{d['amount']}｜来源：{d['src']}")
        else:
            md.append("- 今日未抓到标题含“融资”的条目（或均被噪音规则排除）。")

        md.append("\n## 🏦 VC/PE 基金动态（≤10）")
        if funds:
            for i, f in enumerate(funds, 1):
                md.append(f"{i}. **[{f['title']}]({f['link']})**")
                md.append(f"   - 规模线索：{f['amount']}｜来源：{f['src']}")
        else:
            md.append("- 今日未抓到明确募资/设立/备案类标题。")

        md.append("\n## 🌍 海外对比（≤5）")
        md.append("### 抓取诊断（海外源）")
        md.extend(diag_os)
        if overseas:
            md.append("")
            for o in overseas:
                md.append(f"- **[{o['title']}]({o['link']})**")
        else:
            md.append("- 今日未抓到海外融资条目（或当天 funding 文章较少）。")

        post_to_serverchan(f"{today} 股权投融资晨报", "\n".join(md))

    except Exception as ex:
        post_to_serverchan(
            f"{today} 晨报失败告警",
            f"# {today} 晨报生成失败（已捕获）\n\n- 错误类型：{type(ex).__name__}\n- 错误信息：{ex}\n"
        )

if __name__ == "__main__":
    main()
