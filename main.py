import os
import re
import datetime
import requests
import feedparser

# ======================
#  数据源（尽量选“投融资密度高 + 相对稳定”的）
#  注意：RSSHub 公共实例偶尔波动，所以要做诊断输出
# ======================

CHINA_FEEDS = [
    # 36氪：投融资专栏（密度更高）
    "https://rsshub.app/36kr/investment",
    # 36氪快讯（补充）
    "https://rsshub.app/36kr/newsflashes",
    # 创业邦：投融资标签
    "https://rsshub.app/cyzone/label/投融资",
    # 创业邦：投融资周报（补充密度）
    "https://rsshub.app/cyzone/label/投融资周报",
]

# 海外对比（少量即可）
OVERSEAS_FEEDS = [
    "https://techcrunch.com/tag/funding/feed/",
    "https://www.fiercebiotech.com/rss/xml",
]

# ======================
#  B策略强过滤：宁可少也要真融资
# ======================

# 动作词（必须出现其一）
ACTION_WORDS = ["完成", "获", "获得", "宣布完成", "宣布获得", "完成了", "完成近", "完成约", "完成超"]
# 轮次/融资词（必须出现其一）
ROUND_WORDS = [
    "融资", "天使轮", "种子轮", "Pre-A", "PreA", "A轮", "A+轮", "B轮", "C轮", "D轮", "E轮",
    "战略融资", "并购", "收购"
]
# 噪音排除
NOISE_WORDS = ["论坛", "峰会", "活动", "会议", "报告", "白皮书", "观点", "盘点", "预测", "招聘", "发布会"]

# 基金/募资强信号（标题里出现即可算“基金动态”，但也排噪音）
FUND_WORDS = ["募资", "募集", "首关", "终关", "设立", "成立", "备案", "基金", "GP", "LP"]

# 赛道：用于分组（非赛道一律剔除，符合你“AI/医疗/硬科技/消费/前沿科技”的要求）
SECTOR_RULES = {
    "AI": ["AI", "人工智能", "大模型", "LLM", "AIGC", "多模态", "算力", "机器人", "具身", "自动驾驶"],
    "医疗/生物": ["医疗", "医药", "生物", "器械", "IVD", "基因", "细胞", "抗体", "肿瘤", "诊断", "制药"],
    "硬科技": ["芯片", "半导体", "EDA", "材料", "先进制造", "工业", "传感", "光电", "储能", "电池", "航空航天"],
    "前沿科技": ["量子", "脑机", "BCI", "核聚变", "空间", "卫星", "合成生物", "新材料", "超导"],
    "消费": ["新消费", "品牌", "零售", "连锁", "餐饮", "咖啡", "美妆", "母婴", "潮玩", "宠物", "饮料", "食品"],
}

AMOUNT_RE = re.compile(r"((?:超|近|约)?\s*\d+(?:\.\d+)?\s*(?:亿|万)?\s*(?:人民币|元|美元|美金|US\$|USD|RMB)?)", re.I)

def clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()

def has_any(text, keys):
    t = (text or "").lower()
    return any(k.lower() in t for k in keys)

def detect_sector(text):
    for sector, keys in SECTOR_RULES.items():
        if has_any(text, keys):
            return sector
    return "其他"

def extract_amount(text):
    m = AMOUNT_RE.search((text or "").replace(",", ""))
    return m.group(1).strip() if m else "未披露"

def parse_feed(url, limit=50):
    """
    关键：用 requests + timeout 取内容，避免 feedparser 直接卡死或无声失败
    返回：items, status
    """
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
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

def is_true_deal(title):
    if not title:
        return False
    if has_any(title, NOISE_WORDS):
        return False
    return has_any(title, ACTION_WORDS) and has_any(title, ROUND_WORDS)

def is_fund_news(title):
    if not title:
        return False
    if has_any(title, NOISE_WORDS):
        return False
    return has_any(title, FUND_WORDS)

def post_to_serverchan(title, desp_md):
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

        # 2) 过滤真融资（并按赛道）
        deals = []
        funds = []
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
                # 基金动态不做赛道要求
                funds.append({
                    "title": title,
                    "link": it["link"],
                    "amount": extract_amount(blob),
                    "src": it.get("_src", ""),
                })

        # 去重 + 控制数量（B策略：宁可少）
        deals = list({d["title"]: d for d in deals}.values())[:15]
        funds = list({f["title"]: f for f in funds}.values())[:10]

        # 3) 海外对比（轻量）
        overseas = []
        for url in OVERSEAS_FEEDS:
            items, st = parse_feed(url, limit=25)
            # 海外只做“funding”等弱过滤
            for it in items:
                blob = (it["title"] + " " + it["summary"]).lower()
                if any(k in blob for k in ["funding", "financing", "raised", "series", "seed", "round"]):
                    overseas.append(it)
        overseas = list({o["title"]: o for o in overseas}.values())[:5]

        # 4) 输出（关键：就算 0 条，也要把“抓取诊断 + 原始样本标题”发出来）
        md = []
        md.append(f"# {today} VC/PE 融资晨报（B策略：宁可少也要真）\n")

        md.append("## ✅ 抓取诊断（非常重要）")
        md.extend(diag)
        md.append("")

        md.append("## 🇨🇳 中国真融资（≤15）")
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(f"{i}. **[{d['title']}]({d['link']})**")
                md.append(f"   - 赛道：{d['sector']}｜金额：{d['amount']}")
        else:
            md.append("- 今日未筛到“标题明确完成融资/轮次”的条目。")
            md.append("- 下面给你 10 条原始标题样本（用于判断是源问题还是过滤太严）：")
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
        if overseas:
            for o in overseas:
                md.append(f"- **[{o['title']}]({o['link']})**")
        else:
            md.append("- 今日未抓到海外融资条目。")

        post_to_serverchan(f"{today} VC/PE 晨报", "\n".join(md))

    except Exception as ex:
        # 兜底：任何异常都要发到微信
        err_md = f"# {today} 晨报生成失败（已捕获）\n\n- 错误类型：{type(ex).__name__}\n- 错误信息：{ex}\n\n请到 GitHub Actions 日志查看详细报错。"
        post_to_serverchan(f"{today} 晨报失败告警", err_md)

if __name__ == "__main__":
    main()
