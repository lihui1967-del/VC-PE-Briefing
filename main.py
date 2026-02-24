import os, re, datetime
import requests
import feedparser

# ========== 数据源（先保守，后续可继续加）==========
# 这些源偏“新闻快讯/周报”，但我们用“强过滤”只留下真融资标题
CHINA_FEEDS = [
    CHINA_FEEDS = [

    # 36氪 投融资专栏
    "https://rsshub.app/36kr/investment",

    # 创业邦 投融资
    "https://rsshub.app/cyzone/label/投融资",

    # 投资界 投融资事件
    "https://rsshub.app/pedaily/weeklyinvest",

    # 猎云网 融资
    "https://rsshub.app/lieyunwang/news/融资",

    # 亿欧 投融资
    "https://rsshub.app/iyiou/invest",

]
]

OVERSEAS_FEEDS = [
    "https://techcrunch.com/tag/funding/feed/",
    "https://www.fiercebiotech.com/rss/xml",
]

# ========== 赛道关键词（用于分组，不作为“是否融资”的唯一依据）==========
SECTOR_RULES = {
    "AI": ["AI", "人工智能", "大模型", "LLM", "AIGC", "多模态", "算力", "机器人", "具身", "自动驾驶"],
    "医疗/生物": ["医疗", "医药", "生物", "器械", "IVD", "基因", "细胞", "抗体", "肿瘤", "诊断", "制药"],
    "硬科技": ["芯片", "半导体", "EDA", "材料", "先进制造", "工业", "传感", "光电", "储能", "电池", "航空航天"],
    "前沿科技": ["量子", "脑机", "BCI", "核聚变", "空间", "卫星", "合成生物", "新材料", "超导"],
    "消费": ["新消费", "品牌", "零售", "连锁", "餐饮", "咖啡", "美妆", "母婴", "潮玩", "宠物", "饮料", "食品"],
}

# ========== “真融资/真募资”强信号（标题必须满足）==========
# 1) 必须出现融资动作词
ACTION_WORDS = ["完成", "获", "获得", "宣布完成", "宣布获得", "完成了", "完成近", "完成超", "完成约"]
# 2) 必须出现融资/轮次词
ROUND_WORDS = [
    "融资", "天使轮", "种子轮", "Pre-A", "PreA", "A轮", "A+轮", "B轮", "C轮", "D轮", "E轮", "战略融资", "并购", "收购"
]
# 3) 噪音排除词（出现就剔除）
NOISE_WORDS = ["论坛", "峰会", "活动", "会议", "报告", "白皮书", "观点", "解读", "盘点", "观察", "预测", "招聘", "发布会"]

# 基金募资强信号
FUND_STRONG = ["募资", "募集", "首关", "终关", "设立", "备案", "基金"]
FUND_ACTION = ["完成", "宣布", "获", "设立", "成立"]
FUND_NOISE = ["观点", "论坛", "峰会", "报告", "解读", "盘点"]

# 轮次与阶段
ROUND_PATTERNS = [
    (r"(种子轮|天使轮|天使\+轮|天使\+)", "早期"),
    (r"(Pre-?A\+{0,3}|PreA\+{0,3}|A\+{0,3}轮|A轮)", "成长期"),
    (r"(B\+{0,3}轮|B轮|C轮|D轮|E轮)", "扩张期"),
    (r"(战略融资|并购|收购|IPO|上市)", "后期/退出"),
]

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

def detect_round(text: str) -> str:
    for pat, group in ROUND_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return f"{m.group(1)}（{group}）"
    return "未标注"

def extract_amount(text: str) -> str:
    m = AMOUNT_RE.search((text or "").replace(",", ""))
    return m.group(1).strip() if m else "未披露"

def amount_to_rmb(amount_str: str) -> float | None:
    if not amount_str or amount_str == "未披露":
        return None
    s = amount_str.replace(" ", "")
    fx = 7.2  # 粗算：仅用于趋势统计
    is_usd = any(x in s.lower() for x in ["美元", "美金", "usd", "us$"])
    is_rmb = any(x in s.lower() for x in ["人民币", "rmb", "元"]) and not is_usd

    num = re.search(r"\d+(?:\.\d+)?", s)
    if not num:
        return None
    v = float(num.group(0))
    if "亿" in s:
        base = v * 1e8
    elif "万" in s:
        base = v * 1e4
    else:
        base = v

    if is_usd:
        return base * fx
    if is_rmb:
        return base
    return None  # 不明确币种不计入统计

def parse_feed(url: str, limit=40):
    d = feedparser.parse(url)
    out = []
    for e in d.entries[:limit]:
        title = clean(getattr(e, "title", ""))
        link = getattr(e, "link", "")
        summary = clean(getattr(e, "summary", ""))[:220]
        published = clean(getattr(e, "published", "")) or clean(getattr(e, "updated", ""))
        out.append({"title": title, "link": link, "summary": summary, "published": published})
    return out

def is_true_deal(title: str) -> bool:
    if not title:
        return False
    if has_any(title, NOISE_WORDS):
        return False
    return has_any(title, ACTION_WORDS) and has_any(title, ROUND_WORDS)

def is_true_fund(title: str) -> bool:
    if not title:
        return False
    if has_any(title, FUND_NOISE):
        return False
    return has_any(title, FUND_ACTION) and has_any(title, FUND_STRONG)

def pick_deals():
    pool = []
    for u in CHINA_FEEDS:
        pool.extend(parse_feed(u))
    deals = []
    for it in pool:
        t = it["title"]
        if is_true_deal(t):
            blob = f"{t} {it['summary']}"
            sector = detect_sector(blob)
            if sector == "其他":
                # B策略：宁可少也要真融资，但你关注赛道为主；非赛道剔除
                continue
            it["sector"] = sector
            it["round"] = detect_round(blob)
            it["amount"] = extract_amount(blob)
            deals.append(it)
    # 去重
    seen, uniq = set(), []
    for d in deals:
        key = (d["title"], d["link"])
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq[:15]  # B策略：宁可少，最多15条

def pick_funds():
    pool = []
    for u in CHINA_FEEDS:
        pool.extend(parse_feed(u))
    funds = []
    for it in pool:
        if is_true_fund(it["title"]):
            blob = f"{it['title']} {it['summary']}"
            it["amount"] = extract_amount(blob)
            funds.append(it)
    seen, uniq = set(), []
    for f in funds:
        key = (f["title"], f["link"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq[:8]

def pick_overseas():
    pool = []
    for u in OVERSEAS_FEEDS:
        pool.extend(parse_feed(u, limit=25))
    keys = ["funding", "financing", "raised", "series a", "series b", "seed", "round"]
    picked = [it for it in pool if has_any((it["title"] + " " + it["summary"]), keys)]
    seen, uniq = set(), []
    for o in picked:
        key = (o["title"], o["link"])
        if key not in seen:
            seen.add(key)
            uniq.append(o)
    return uniq[:5]

def build_stats(deals):
    total = len(deals)
    by_sector, by_stage = {}, {}
    disclosed_count, disclosed_sum = 0, 0.0
    for d in deals:
        by_sector[d["sector"]] = by_sector.get(d["sector"], 0) + 1
        stage = "未标注"
        if "早期" in d["round"]:
            stage = "早期"
        elif "成长期" in d["round"]:
            stage = "成长期"
        elif "扩张期" in d["round"]:
            stage = "扩张期"
        elif "后期/退出" in d["round"]:
            stage = "后期/退出"
        by_stage[stage] = by_stage.get(stage, 0) + 1

        rmb = amount_to_rmb(d["amount"])
        if rmb is not None:
            disclosed_count += 1
            disclosed_sum += rmb

    return {
        "total": total,
        "disclosed_count": disclosed_count,
        "undisclosed": total - disclosed_count,
        "disclosed_sum_rmb": disclosed_sum,
        "by_sector": by_sector,
        "by_stage": by_stage,
    }

def trend_commentary(stats):
    lines = []
    if stats["total"] == 0:
        return ["- 今日未抓到明确融资标题：建议增补投融资专栏RSS源（后续我给你一键添加清单）。"]
    ratio = stats["disclosed_count"] / max(stats["total"], 1)
    if ratio >= 0.5:
        lines.append("- 披露口径较好：可统计金额占比不低，资金强弱信号更清晰。")
    else:
        lines.append("- 披露口径偏弱：金额未披露占比较高，重点看“投资方/轮次”信号。")

    if stats["by_sector"]:
        top_sector = max(stats["by_sector"].items(), key=lambda x: x[1])
        lines.append(f"- 今日赛道热度：**{top_sector[0]}**（{top_sector[1]}条）相对更集中。")

    if stats["by_stage"]:
        top_stage = max(stats["by_stage"].items(), key=lambda x: x[1])
        lines.append(f"- 轮次结构：**{top_stage[0]}**占比更高，反映市场风险偏好与确定性取向。")
    return lines[:3]

def watchlist(deals, funds):
    # 只从“真融资”里选：偏硬科技/前沿、偏后期、金额披露、出现领投/战略信号
    scored = []
    for d in deals:
        score = 0
        t = d["title"]
        if d["sector"] in ["硬科技", "前沿科技"]:
            score += 3
        if "扩张期" in d["round"] or "后期/退出" in d["round"]:
            score += 2
        if d["amount"] != "未披露":
            score += 1
        if has_any(t, ["领投", "战略", "国资", "产业"]):
            score += 2
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    wl_deals = [d for _, d in scored[:4]]
    wl_fund = funds[0] if funds else None
    return wl_deals, wl_fund

def post_to_serverchan(title: str, md: str):
    sendkey = os.environ["SENDKEY"]
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    r = requests.post(url, data={"title": title, "desp": md}, timeout=25)
    r.raise_for_status()

def main():
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    deals = pick_deals()
    funds = pick_funds()
    overseas = pick_overseas()
    stats = build_stats(deals)
    tlines = trend_commentary(stats)
    wl_deals, wl_fund = watchlist(deals, funds)

    md = []
    md.append(f"# {today} VC/PE & 融资晨报（B策略：宁可少也要真）")
    md.append("")
    md.append("## ① 🇨🇳 中国融资动态（真融资｜≤15）")
    if not deals:
        md.append("- 今日未抓到“标题明确完成融资/轮次”的条目（这在新闻淡日属于正常）。")
    else:
        for i, d in enumerate(deals, 1):
            md.append(f"{i}. **[{d['title']}]({d['link']})**")
            md.append(f"   - 赛道：{d['sector']}｜轮次：{d['round']}｜金额：{d['amount']}")
            if d["summary"]:
                md.append(f"   - 摘要：{d['summary']}")

    md.append("")
    md.append("## ② 🌍 海外对比（≤5）")
    if not overseas:
        md.append("- 今日未抓到海外融资条目。")
    else:
        for i, o in enumerate(overseas, 1):
            md.append(f"{i}. **[{o['title']}]({o['link']})**")
            if o["summary"]:
                md.append(f"   - {o['summary']}")

    md.append("")
    md.append("## ③ 📊 融资规模统计汇总（披露口径）")
    md.append(f"- 今日真融资事件：**{stats['total']}** 条")
    md.append(f"- 金额披露：**{stats['disclosed_count']}** 条｜未披露：**{stats['undisclosed']}** 条")
    if stats["disclosed_sum_rmb"] > 0:
        md.append(f"- 披露金额合计（粗算）：约 **{stats['disclosed_sum_rmb']/1e8:.2f} 亿元人民币**（趋势用途）")
    if stats["by_sector"]:
        md.append("- 分赛道（条数）： " + "｜".join([f"{k}{v}" for k, v in sorted(stats["by_sector"].items(), key=lambda x: x[1], reverse=True)]))
    if stats["by_stage"]:
        md.append("- 分阶段（条数）： " + "｜".join([f"{k}{v}" for k, v in sorted(stats["by_stage"].items(), key=lambda x: x[1], reverse=True)]))

    md.append("")
    md.append("## ④ 🏦 VC/PE 基金动态（真募资/设立｜≤8）")
    if not funds:
        md.append("- 今日未抓到“募资/首关/终关/设立/备案”类明确标题。")
    else:
        for i, f in enumerate(funds, 1):
            md.append(f"{i}. **[{f['title']}]({f['link']})**")
            md.append(f"   - 规模/线索：{extract_amount(f['title'] + ' ' + f['summary'])}")

    md.append("")
    md.append("## ⑤ 🔥 热点趋势点评（3条）")
    md.extend(tlines)

    md.append("")
    md.append("## ⑥ 🎯 明日关注清单（≤5）")
    if not wl_deals and not wl_fund:
        md.append("- 暂无（信息量不足）。")
    else:
        for d in wl_deals:
            md.append(f"- **[{d['title']}]({d['link']})**（{d['sector']}｜{d['round']}）— 关注投资方/金额后续披露。")
        if wl_fund:
            md.append(f"- **[{wl_fund['title']}]({wl_fund['link']})** — 关注GP/LP与投资策略披露。")

    post_to_serverchan(f"{today} VC/PE & 融资晨报", "\n".join(md))

if __name__ == "__main__":
    main()
