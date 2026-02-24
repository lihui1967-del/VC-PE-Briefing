import os, re, datetime
import requests
import feedparser

# --------- 1) RSS 源（免费：RSSHub + 少量海外RSS）---------
# 说明：rsshub.app 是 RSSHub 公共实例；若不稳定，后续可换成你自己的 RSSHub 部署地址
CHINA_FEEDS = [
    # 36氪快讯（经常有融资）
    "https://rsshub.app/36kr/newsflashes",
    # 创业邦：投融资周报（移动站标签页，信息密度高）
    "https://rsshub.app/cyzone/label/%E6%8A%95%E8%9E%8D%E8%B5%84%E5%91%A8%E6%8A%A5",
    # 创业邦：全球投融资周报（同标签，可能会混入海外，对比用）
    "https://rsshub.app/cyzone/label/%E5%85%A8%E7%90%83%E6%8A%95%E8%9E%8D%E8%B5%84%E5%91%A8%E6%8A%A5",
]

FUND_FEEDS = [
    # 36氪/创业邦里也会有“基金/募资/设立/备案”类
    "https://rsshub.app/36kr/newsflashes",
    "https://rsshub.app/cyzone/label/%E6%8A%95%E8%9E%8D%E8%B5%84%E5%91%A8%E6%8A%A5",
]

OVERSEAS_FEEDS = [
    # 海外对比：尽量选公开RSS（这里给两条稳定的科技/融资类信息源）
    "https://techcrunch.com/tag/funding/feed/",
    "https://www.fiercebiotech.com/rss/xml",  # 医疗/生物科技融资更常出现
]

# --------- 2) 关键词与分类 ----------
SECTOR_RULES = {
    "AI": ["AI", "人工智能", "大模型", "LLM", "AIGC", "多模态", "算力", "推理", "机器人", "具身", "自动驾驶"],
    "医疗/生物": ["医疗", "医药", "生物", "器械", "IVD", "基因", "细胞", "抗体", "肿瘤", "诊断", "制药"],
    "硬科技": ["芯片", "半导体", "EDA", "光刻", "材料", "先进制造", "工业", "传感", "光电", "储能", "电池", "航空航天"],
    "前沿科技": ["量子", "脑机", "BCI", "核聚变", "空间", "卫星", "合成生物", "新材料", "超导"],
    "消费": ["新消费", "品牌", "零售", "连锁", "餐饮", "咖啡", "美妆", "母婴", "潮玩", "宠物", "饮料", "食品"],
}

# 触发“融资新闻”的强信号（减少误报）
DEAL_SIGNALS = [
    "融资", "完成", "获", "披露", "投资", "领投", "跟投",
    "天使轮", "种子轮", "Pre-A", "A轮", "A+轮", "B轮", "C轮", "D轮", "E轮", "战略融资", "并购",
]
FUND_SIGNALS = ["基金", "募资", "募集", "首关", "终关", "设立", "备案", "GP", "LP", "管理人", "私募"]

ROUND_PATTERNS = [
    (r"(种子轮|天使轮|天使\+轮|天使\+)", "早期"),
    (r"(Pre-?A\+{0,3}|PreA\+{0,3}|A\+{0,3}轮|A轮)", "成长期"),
    (r"(B\+{0,3}轮|B轮|C轮|D轮|E轮)", "扩张期"),
    (r"(战略融资|并购|收购|IPO|上市)", "后期/退出"),
]

AMOUNT_RE = re.compile(r"((?:超|近|约)?\s*\d+(?:\.\d+)?\s*(?:亿|万)?\s*(?:人民币|元|美元|美金|US\\$|USD|RMB)?)")

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
        if re.search(pat, text, re.IGNORECASE):
            # 取更具体的轮次片段
            m = re.search(pat, text, re.IGNORECASE)
            return f"{m.group(1)}（{group}）"
    return "未标注"

def extract_amount(text: str) -> str:
    m = AMOUNT_RE.search(text.replace(",", ""))
    return m.group(1).strip() if m else "未披露"

def amount_to_rmb(amount_str: str) -> float | None:
    """
    只做非常保守的“披露口径粗算”：
    - 识别 “xx亿/万 人民币/元”
    - 识别 “xx亿/万 美元/美金/USD” -> 以 1 USD = 7.2 RMB 粗算（用于统计趋势，不用于精确财务）
    """
    if not amount_str or amount_str == "未披露":
        return None
    s = amount_str.replace(" ", "")
    fx = 7.2
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
        base = v  # 兜底：当作元/美元

    if is_usd:
        return base * fx
    if is_rmb:
        return base
    # 未写币种：不计入金额统计（避免误算）
    return None

def parse_feed_items(url: str, max_per_feed=25):
    d = feedparser.parse(url)
    items = []
    for e in d.entries[:max_per_feed]:
        title = clean(getattr(e, "title", ""))
        link = getattr(e, "link", "")
        summary = clean(getattr(e, "summary", ""))[:220]
        published = clean(getattr(e, "published", "")) or clean(getattr(e, "updated", ""))
        blob = f"{title} {summary}"
        items.append({"title": title, "link": link, "summary": summary, "published": published, "blob": blob})
    return items

def pick_china_deals():
    pool = []
    for u in CHINA_FEEDS:
        pool.extend(parse_feed_items(u))
    # 强信号过滤：既要命中赛道关键词，又要命中融资信号
    picked = []
    for it in pool:
        blob = it["blob"]
        if detect_sector(blob) != "其他" and has_any(blob, DEAL_SIGNALS):
            it["sector"] = detect_sector(blob)
            it["round"] = detect_round(blob)
            it["amount"] = extract_amount(blob)
            picked.append(it)
    # 去重：标题+链接
    uniq, seen = [], set()
    for it in picked:
        key = (it["title"], it["link"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq[:20]

def pick_fund_news():
    pool = []
    for u in FUND_FEEDS:
        pool.extend(parse_feed_items(u))
    picked = []
    for it in pool:
        blob = it["blob"]
        if has_any(blob, FUND_SIGNALS) and has_any(blob, ["募资", "募集", "设立", "备案", "首关", "终关", "基金"]):
            it["amount"] = extract_amount(blob)
            picked.append(it)
    uniq, seen = [], set()
    for it in picked:
        key = (it["title"], it["link"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq[:8]

def pick_overseas():
    pool = []
    for u in OVERSEAS_FEEDS:
        pool.extend(parse_feed_items(u, max_per_feed=15))
    # 海外对比：更宽松，只要命中 funding/financing/raised 等
    keys = ["funding", "financing", "raised", "series a", "series b", "seed", "round"]
    picked = [it for it in pool if has_any(it["blob"], keys)]
    uniq, seen = [], set()
    for it in picked:
        key = (it["title"], it["link"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq[:5]

def build_stats(deals):
    by_sector = {}
    by_stage = {}
    disclosed_count = 0
    disclosed_sum = 0.0
    for d in deals:
        by_sector[d["sector"]] = by_sector.get(d["sector"], 0) + 1
        # stage from round
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
    total = len(deals)
    undisclosed = total - disclosed_count
    return {
        "total": total,
        "disclosed_count": disclosed_count,
        "undisclosed": undisclosed,
        "disclosed_sum_rmb": disclosed_sum,
        "by_sector": by_sector,
        "by_stage": by_stage,
    }

def trend_commentary(stats):
    # 轻量“趋势点评”：用结构信号给3条可读结论（不瞎编具体金额）
    lines = []
    # 1) 披露比例
    if stats["total"] > 0:
        ratio = stats["disclosed_count"] / stats["total"]
        if ratio >= 0.6:
            lines.append("- 披露信息相对充分：今天可统计的金额占比偏高，适合做资金强弱判断。")
        else:
            lines.append("- 披露信息偏少：更多是“宣布/报道型”动态，金额未披露占比较高，需持续跟踪补全。")
    # 2) 赛道热度
    if stats["by_sector"]:
        top_sector = sorted(stats["by_sector"].items(), key=lambda x: x[1], reverse=True)[0]
        lines.append(f"- 赛道热度集中在 **{top_sector[0]}**（{top_sector[1]}条），资金与叙事继续向头部细分聚集。")
    # 3) 阶段结构
    if stats["by_stage"]:
        top_stage = sorted(stats["by_stage"].items(), key=lambda x: x[1], reverse=True)[0]
        lines.append(f"- 轮次结构上 **{top_stage[0]}** 占比更高，反映市场更偏向该阶段的风险偏好与确定性。")
    return lines[:3] if lines else ["- 今日信息量偏少，建议增加RSS源覆盖面。"]

def watchlist(deals, funds):
    # 规则：优先大额/后期/硬科技/前沿，或出现“领投/战略”等信号
    scored = []
    for d in deals:
        score = 0
        blob = d["blob"]
        if d["sector"] in ["硬科技", "前沿科技"]:
            score += 3
        if "扩张期" in d["round"] or "后期/退出" in d["round"]:
            score += 2
        if has_any(blob, ["领投", "战略", "国资", "产业方"]):
            score += 2
        if d["amount"] != "未披露":
            score += 1
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    picks = [d for _, d in scored[:4]]
    # 补一个基金关注
    if funds:
        picks_fund = funds[0]
        return picks, picks_fund
    return picks, None

def post_to_serverchan(title: str, desp_md: str):
    sendkey = os.environ["SENDKEY"]
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    r = requests.post(url, data={"title": title, "desp": desp_md}, timeout=25)
    r.raise_for_status()

def main():
    # 以UTC+8生成日期
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")

    deals = pick_china_deals()
    funds = pick_fund_news()
    overseas = pick_overseas()
    stats = build_stats(deals)
    tlines = trend_commentary(stats)
    wl_deals, wl_fund = watchlist(deals, funds)

    md = []
    md.append(f"# {today} VC/PE & 融资晨报（中国为主）")
    md.append("")
    md.append("## ① 🇨🇳 中国融资动态（精选≤20）")
    if not deals:
        md.append("- 今日在当前订阅源中未筛到有效融资条目（建议扩充RSS源）。")
    else:
        for i, d in enumerate(deals, 1):
            md.append(f"{i}. **[{d['title']}]({d['link']})**")
            md.append(f"   - 赛道：{d['sector']}｜轮次：{d['round']}｜金额：{d['amount']}")
            if d["summary"]:
                md.append(f"   - 摘要：{d['summary']}")
    md.append("")
    md.append("## ② 🌍 海外对比（精选≤5）")
    if not overseas:
        md.append("- 今日未抓取到海外融资对比条目（可后续增补海外RSS源）。")
    else:
        for i, o in enumerate(overseas, 1):
            md.append(f"{i}. **[{o['title']}]({o['link']})**")
            if o["summary"]:
                md.append(f"   - {o['summary']}")
    md.append("")
    md.append("## ③ 📊 融资规模统计汇总（披露口径）")
    md.append(f"- 今日融资事件：**{stats['total']}** 条")
    md.append(f"- 金额披露：**{stats['disclosed_count']}** 条｜未披露：**{stats['undisclosed']}** 条")
    if stats["disclosed_sum_rmb"] > 0:
        md.append(f"- 披露金额合计（粗算）：约 **{stats['disclosed_sum_rmb']/1e8:.2f} 亿元人民币**（仅用于趋势，不作精确财务口径）")
    if stats["by_sector"]:
        md.append("- 分赛道（条数）： " + "｜".join([f"{k}{v}" for k, v in sorted(stats["by_sector"].items(), key=lambda x: x[1], reverse=True)]))
    if stats["by_stage"]:
        md.append("- 分阶段（条数）： " + "｜".join([f"{k}{v}" for k, v in sorted(stats["by_stage"].items(), key=lambda x: x[1], reverse=True)]))
    md.append("")
    md.append("## ④ 🏦 VC/PE 基金动态（精选≤8）")
    if not funds:
        md.append("- 今日未筛到明显“募资/设立/备案”类基金动态（可扩充基金信息源）。")
    else:
        for i, f in enumerate(funds, 1):
            md.append(f"{i}. **[{f['title']}]({f['link']})**")
            md.append(f"   - 关键信号：{extract_amount(f['blob'])}｜{f['published'] or '时间未标注'}")
            if f["summary"]:
                md.append(f"   - 摘要：{f['summary']}")
    md.append("")
    md.append("## ⑤ 🔥 热点趋势点评（3条）")
    md.extend(tlines)
    md.append("")
    md.append("## ⑥ 🎯 明日关注清单（5条）")
    if not wl_deals and not wl_fund:
        md.append("- 暂无（信息量不足或命中不足）。")
    else:
        if wl_deals:
            for d in wl_deals:
                reason = []
                if d["sector"] in ["硬科技", "前沿科技"]:
                    reason.append("技术壁垒/产业资本可能性高")
                if "扩张期" in d["round"] or "后期/退出" in d["round"]:
                    reason.append("轮次偏后，资金确定性更强")
                if d["amount"] != "未披露":
                    reason.append("金额已披露，利于对标估值")
                md.append(f"- **[{d['title']}]({d['link']})**（{d['sector']}｜{d['round']}）— {('；'.join(reason) if reason else '关注后续披露投资方/金额')}")

        if wl_fund:
            md.append(f"- **[{wl_fund['title']}]({wl_fund['link']})** — 关注其GP/策略/出资人结构的后续披露。")

    post_to_serverchan(f"{today} VC/PE & 融资晨报", "\n".join(md))

if __name__ == "__main__":
    main()
