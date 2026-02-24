import os
import re
import datetime
import requests
import feedparser

# ======================
# 直连 RSS/Atom 源（不走 RSSHub，避免 403）
# ======================

CHINA_FEEDS = [
    # 36氪（全站 RSS，内容较泛，但能作为底池）
    "https://36kr.com/feed",

    # 创业邦（官方 RSS：内容不全但可用）
    "https://www.cyzone.cn/rss",

    # 猎云网（更推荐这两个：社区长期使用的直连 RSS）
    "http://www.lieyunwang.com/feed",             # :contentReference[oaicite:1]{index=1}
    "http://www.lieyunwang.com/newrss/feed.xml",  # :contentReference[oaicite:2]{index=2}

    # 钛媒体（偏产业/科技，也会出现融资报道）
    "http://www.tmtpost.com/rss.xml",             # :contentReference[oaicite:3]{index=3}

    # 动点科技（中国科技/融资也比较多）
    "https://cn.technode.com/feed/",              # :contentReference[oaicite:4]{index=4}
]

OVERSEAS_FEEDS = [
    "https://techcrunch.com/tag/funding/feed/",
    "https://www.fiercebiotech.com/rss/xml",
]

# ======================
# B策略：宁可少，也要“标题级真融资”
# ======================

# 标题必须命中：融资词 + 轮次/金额/投资方信号（至少其一）
DEAL_CORE = ["融资", "获融资", "完成融资", "追加融资", "战略融资"]
ROUND_WORDS = ["天使轮", "种子轮", "Pre-A", "PreA", "A轮", "A+轮", "B轮", "C轮", "D轮", "E轮"]
INVESTOR_WORDS = ["领投", "跟投", "投资", "加持"]
AMOUNT_WORDS = ["亿", "万", "美元", "美金", "人民币", "RMB", "USD"]

# 排除噪音（出现就直接剔除）
NOISE_WORDS = ["论坛", "峰会", "活动", "会议", "报告", "白皮书", "观点", "盘点", "预测", "招聘", "发布会", "开幕", "闭幕"]

# 基金动态（标题级）
FUND_WORDS = ["募资", "募集", "首关", "终关", "设立", "成立", "备案", "基金", "GP", "LP"]

# 你关注的赛道（用于分组 + 非赛道剔除）
SECTOR_RULES = {
    "AI": ["AI", "人工智能", "大模型", "LLM", "AIGC", "多模态", "算力", "机器人", "具身", "自动驾驶"],
    "医疗/生物": ["医疗", "医药", "生物", "器械", "IVD", "基因", "细胞", "抗体", "肿瘤", "诊断", "制药"],
    "硬科技": ["芯片", "半导体", "EDA", "材料", "先进制造", "工业", "传感", "光电", "储能", "电池", "航空航天"],
    "前沿科技": ["量子", "脑机", "BCI", "核聚变", "空间", "卫星", "合成生物", "新材料", "超导"],
    "消费": ["新消费", "品牌", "零售", "连锁", "餐饮", "咖啡", "美妆", "母婴", "潮玩", "宠物", "饮料", "食品"],
}

AMOUNT_RE = re.compile(r"((?:超|近|约)?\s*\d+(?:\.\d+)?\s*(?:亿|万)?\s*(?:人民币|元|美元|美金|US\$|USD|RMB)?)", re.I)

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
    return "其他"

def extract_amount(text: str) -> str:
    m = AMOUNT_RE.search((text or "").replace(",", ""))
    return m.group(1).strip() if m else "未披露"

def parse_feed(url: str, limit=50):
    """
    返回: items(list), status(str)
    """
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        items = []
        for e in feed.entries[:limit]:
            items.append({
                "title": clean(getattr(e, "title", "")),
                "link": getattr(e, "link", ""),
                "summary": clean(getattr(e, "summary", ""))[:220],
            })
        return items, f"OK({len(items)})"
    except Exception as ex:
        return [], f"FAIL({type(ex).__name__}) {ex}"

def is_true_deal(title: str) -> bool:
    if not title:
        return False
    if has_any(title, NOISE_WORDS):
        return False

    # 必须有“融资”核心词
    if not has_any(title, DEAL_CORE) and "融资" not in title:
        return False

    # 再要求至少命中：轮次 / 金额信号 / 投资方信号（防止“融资观点/融资课”）
    if has_any(title, ROUND_WORDS) or has_any(title, AMOUNT_WORDS) or has_any(title, INVESTOR_WORDS):
        return True

    return False

def is_fund_news(title: str) -> bool:
    if not title:
        return False
    if has_any(title, NOISE_WORDS):
        return False
    return has_any(title, FUND_WORDS)

def post_to_serverchan(title: str, desp_md: str):
    sendkey = os.environ["SENDKEY"]
    api = f"https://sctapi.ftqq.com/{sendkey}.send"
    r = requests.post(api, data={"title": title, "desp": desp_md}, timeout=20)
    r.raise_for_status()

def main():
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

    try:
        # 1) 抓取 + 诊断
        diag = []
        all_items = []
        for url in CHINA_FEEDS:
            items, st = parse_feed(url)
            diag.append(f"- {url} -> {st}")
            for it in items:
                it["_src"] = url
            all_items.extend(items)

        # 2) 过滤真融资 + 真基金
        deals, funds = [], []
        for it in all_items:
            title = it["title"]
            blob = f"{title} {it['summary']}"

            if is_true_deal(title):
                sector = detect_sector(blob)
                if sector != "其他":
                    deals.append({
                        "title": title,
                        "link": it["link"],
                        "sector": sector,
                        "amount": extract_amount(blob),
                        "src": it.get("_src", ""),
                    })
            elif is_fund_news(title):
                funds.append({
                    "title": title,
                    "link": it["link"],
                    "amount": extract_amount(blob),
                    "src": it.get("_src", ""),
                })

        # 去重 + 控制数量（B策略：宁可少）
        deals = list({d["title"]: d for d in deals}.values())[:15]
        funds = list({f["title"]: f for f in funds}.values())[:10]

        # 3) 海外对比（恢复）
        overseas_pool = []
        odiag = []
        for url in OVERSEAS_FEEDS:
            items, st = parse_feed(url, limit=25)
            odiag.append(f"- {url} -> {st}")
            overseas_pool.extend(items)

        overseas = []
        for it in overseas_pool:
            blob = (it["title"] + " " + it["summary"]).lower()
            if any(k in blob for k in ["funding", "financing", "raised", "series", "seed", "round"]):
                overseas.append(it)
        overseas = list({o["title"]: o for o in overseas}.values())[:5]

        # 4) 输出
        md = []
        md.append(f"# {today} VC/PE 融资晨报（B策略：宁可少也要真）\n")

        md.append("## ✅ 抓取诊断（中国源）")
        md.extend(diag)
        md.append("")

        md.append("## 🇨🇳 中国真融资（≤15）")
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(f"{i}. **[{d['title']}]({d['link']})**")
                md.append(f"   - 赛道：{d['sector']}｜金额：{d['amount']}")
        else:
            md.append("- 今日未筛到“标题级真融资”条目。")
            md.append("- 原始标题样本（前10条，用于判断是否需要再加源/再调规则）：")
            for i, it in enumerate(all_items[:10], 1):
                md.append(f"  {i}) {it['title']}")

        md.append("\n## 🏦 基金/募资动态（≤10）")
        if funds:
            for i, f in enumerate(funds, 1):
                md.append(f"{i}. **[{f['title']}]({f['link']})**")
                md.append(f"   - 规模线索：{f['amount']}")
        else:
            md.append("- 今日未筛到明确募资/设立/备案类标题。")

        md.append("\n## 🌍 海外对比（≤5）")
        md.append("### 抓取诊断（海外源）")
        md.extend(odiag)
        if overseas:
            md.append("")
            for o in overseas:
                md.append(f"- **[{o['title']}]({o['link']})**")
        else:
            md.append("- 今日未抓到海外融资条目（可能是源当天没有 funding 文章，或抓取失败）。")

        post_to_serverchan(f"{today} VC/PE 晨报", "\n".join(md))

    except Exception as ex:
        err_md = f"# {today} 晨报生成失败（已捕获）\n\n- 错误类型：{type(ex).__name__}\n- 错误信息：{ex}\n\n请到 GitHub Actions 日志查看详细报错。"
        post_to_serverchan(f"{today} 晨报失败告警", err_md)

if __name__ == "__main__":
    main()
