#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股权投融资 Daily Briefing

相比初版的主要改动：
  1. 时间窗过滤（RSS 用 published_parsed，HTML 用 URL 里的年月）
  2. state/seen.json 跨天去重，避免同一条新闻连推数日
  3. 收紧融资判定：光有"获/完成"不算，必须落在融资语境里
  4. 修复金额正则（原版量级词可选，等于匹配任何裸数字）
  5. 基金新闻优先于融资判定，修正错分
  6. 信源健康统计，写进推送标题
  7. 产出落盘到 out/，供 GitHub Actions 上传 artifact
  8. 可选的模型精炼层（设了 ANTHROPIC_API_KEY 才启用，失败自动降级）
"""

import os
import re
import sys
import json
import time
import html
import hashlib
import pathlib
import datetime
from typing import List, Dict, Tuple

import requests
import feedparser
from bs4 import BeautifulSoup

# ======================================================================
# 配置区
# ======================================================================

# 只收这个小时数以内的条目。设 36 是为了容忍 RSS 时间戳不准和跑批延迟。
MAX_AGE_HOURS = 36

# 去重记录保留天数
SEEN_RETENTION_DAYS = 21

# 单源请求超时（秒）
REQ_TIMEOUT = 20

# 输出条数上限
MAX_DEALS = 20
MAX_FUNDS = 10
MAX_OVERSEAS = 6

UA = "Mozilla/5.0 (compatible; DailyVCBriefing/2.0)"

STATE_PATH = pathlib.Path("state/seen.json")
OUT_DIR = pathlib.Path("out")

OVERSEAS_FEEDS = [
    ("TechCrunch Funding", "https://techcrunch.com/tag/funding/feed/"),
    ("FierceBiotech", "https://www.fiercebiotech.com/rss/xml"),
]

CHINA_RSS_FEEDS = [
    ("36氪", "https://36kr.com/feed"),
    ("钛媒体", "https://www.tmtpost.com/rss.xml"),
    ("动点科技", "https://cn.technode.com/feed/"),
]

# HTML 抓取源。article_re 用来把导航栏/页脚/推荐位的链接挡在外面，
# 只保留真正的文章页 —— 这个正则务必按站点实际 URL 形态实测调整。
CHINA_HTML_SOURCES = [
    {
        "name": "投资界",
        "url": "https://news.pedaily.cn/",
        "base": "https://news.pedaily.cn",
        # 形如 /202608/123456.shtml
        "article_re": re.compile(r"/(\d{6})/\d+\.shtml"),
        # 第 1 个捕获组是 YYYYMM，用于时间过滤；没有就设 None
        "date_group": 1,
    },
]

# ----------------------------------------------------------------------
# 关键词规则
# ----------------------------------------------------------------------

NOISE_WORDS = [
    "论坛", "峰会", "活动", "会议", "白皮书", "盘点", "预测", "招聘", "发布会",
    "圆桌", "直播", "训练营", "课程", "研讨会", "沙龙", "榜单", "年会", "专访",
    "解读", "观察", "方法论", "招商", "评选", "颁奖",
]

# 轮次/交易语境词。命中其一即可认定是融资语境。
ROUND_WORDS = [
    "天使轮", "种子轮", "Pre-A", "Pre-B", "PreA", "A轮", "B轮", "C轮", "D轮",
    "E轮", "F轮", "Pre-IPO", "战略融资", "战略投资", "轮融资", "融资", "增资",
    "并购", "收购", "领投", "跟投", "估值",
]

# 动作词。单独出现不足以判定，需要配合金额或轮次词。
DEAL_ACTION_WORDS = [
    "获", "完成", "加持", "投资", "注资", "入股", "出资", "融到", "签约",
]

# "获X"型假阳性：获评/获批/获奖/获客/获得认证……
FALSE_POSITIVE_RE = re.compile(
    r"获(评|批|奖|准|颁|授|客|悉|取|刑|赔|救|利|得(认证|授权|资质|专利|许可|批文))"
)

FUND_WORDS = ["募资", "募集", "首关", "终关", "基金", "GP", "LP", "FOF", "母基金", "设立基金"]

SECTOR_RULES = {
    "医疗/生物": ["医疗", "医药", "生物", "器械", "IVD", "基因", "细胞", "抗体",
                  "肿瘤", "诊断", "制药", "疫苗", "临床"],
    "硬科技": ["芯片", "半导体", "EDA", "晶圆", "封装", "传感", "光电", "光刻",
               "储能", "电池", "钙钛矿", "光伏", "氢能", "航空航天", "先进制造",
               "机床", "工业软件", "材料"],
    "前沿科技": ["量子", "脑机", "BCI", "核聚变", "可控核聚变", "卫星", "空间",
                 "合成生物", "超导", "新材料", "碳纤维", "芳纶", "核能"],
    "AI": ["AI", "人工智能", "大模型", "LLM", "AIGC", "多模态", "算力", "机器人",
           "具身", "自动驾驶", "智能体", "Agent"],
    "消费": ["新消费", "品牌", "零售", "连锁", "餐饮", "咖啡", "美妆", "母婴",
             "潮玩", "宠物", "饮料", "食品"],
}

# 金额：量级词必填，避免匹配到年份、公司名里的数字
AMOUNT_RE = re.compile(
    r"(?:超|近|约|逾|达)?\s*"
    r"(?:\d+(?:[.,]\d+)?|数|几|上)\s*"
    r"(?:亿|千万|百万|万)\s*"
    r"(?:人民币|美元|美金|欧元|港元|日元|元|USD|RMB)?"
)

# 海外金额：$50M / $1.2B / US$300 million
AMOUNT_EN_RE = re.compile(
    r"(?:US)?[$€£]\s?\d+(?:\.\d+)?\s?(?:M|B|K|million|billion)\b",
    re.I,
)

OVERSEAS_HIT_WORDS = ["funding", "financing", "raises", "raised", "series a",
                      "series b", "series c", "series d", "seed round",
                      "venture round", "led by"]

# ======================================================================
# 全局状态
# ======================================================================

SRC_STATUS: List[Dict] = []   # [{"name":..., "ok":bool, "n":int, "err":str}]


def log(msg: str) -> None:
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ======================================================================
# 工具函数
# ======================================================================

def clean(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def has_any(text: str, keys) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keys)


def norm_key(title: str) -> str:
    """标题归一化后的指纹，用于跨天去重。"""
    norm = re.sub(r"[\s\W_]+", "", title or "")
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def detect_sector(text: str) -> str:
    # 注意：按 dict 顺序返回首个命中。医疗/硬科技排在 AI 前面，
    # 是为了让"AI 制药""AI 医疗影像"归到行业而非 AI。
    for sector, keys in SECTOR_RULES.items():
        if has_any(text, keys):
            return sector
    return "其他/待归类"


def extract_amount(text: str) -> str:
    t = (text or "")
    m = AMOUNT_RE.search(t)
    if m:
        return m.group(0).strip()
    m = AMOUNT_EN_RE.search(t)
    if m:
        return m.group(0).strip()
    return "未披露"


def is_noise(title: str) -> bool:
    return has_any(title, NOISE_WORDS)


def is_fund_news(title: str) -> bool:
    if not title or is_noise(title):
        return False
    return has_any(title, FUND_WORDS)


def is_true_deal(title: str) -> bool:
    """
    融资判定：不能只看动作词。
      - 命中轮次/交易语境词 → 是
      - 或者：动作词 + 明确金额 → 是
    """
    if not title or is_noise(title):
        return False
    if FALSE_POSITIVE_RE.search(title):
        return False
    if has_any(title, ROUND_WORDS):
        return True
    return has_any(title, DEAL_ACTION_WORDS) and bool(AMOUNT_RE.search(title))


# ======================================================================
# 去重状态
# ======================================================================

def load_seen() -> Dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as ex:
        log(f"seen.json 读取失败，按空处理：{ex}")
        return {}


def save_seen(seen: Dict[str, str]) -> None:
    cutoff = (datetime.date.today() - datetime.timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(pruned, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    log(f"seen.json 已更新：{len(pruned)} 条（清理掉 {len(seen) - len(pruned)} 条过期）")


# ======================================================================
# 抓取
# ======================================================================

def fetch_rss(name: str, url: str, limit: int = 100) -> List[Dict]:
    try:
        r = requests.get(url, timeout=REQ_TIMEOUT, headers={"User-Agent": UA})
        r.raise_for_status()
        d = feedparser.parse(r.content)

        if not d.entries:
            SRC_STATUS.append({"name": name, "ok": False, "n": 0, "err": "解析到 0 条"})
            log(f"⚠ {name}: 解析到 0 条，源可能已失效")
            return []

        items, stale = [], 0
        now = time.time()
        for e in d.entries[:limit]:
            ts = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if ts:
                age_h = (now - time.mktime(ts)) / 3600
                if age_h > MAX_AGE_HOURS:
                    stale += 1
                    continue
            items.append({
                "title": clean(getattr(e, "title", "")),
                "link": getattr(e, "link", ""),
                "summary": clean(getattr(e, "summary", ""))[:240],
                "src": name,
            })

        SRC_STATUS.append({"name": name, "ok": True, "n": len(items), "err": ""})
        log(f"✓ {name}: {len(items)} 条在窗口内（过滤掉 {stale} 条过期）")
        return items

    except Exception as ex:
        SRC_STATUS.append({"name": name, "ok": False, "n": 0, "err": type(ex).__name__})
        log(f"✗ {name}: {type(ex).__name__} — {ex}")
        return []


def fetch_html_links(src: Dict, limit: int = 200) -> List[Dict]:
    name = src["name"]
    try:
        r = requests.get(src["url"], timeout=REQ_TIMEOUT, headers={"User-Agent": UA})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        art_re = src.get("article_re")
        date_group = src.get("date_group")
        # 允许当月和上月（跨月那几天）
        today = datetime.date.today()
        ok_months = {today.strftime("%Y%m"),
                     (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y%m")}

        out, seen_local = [], set()
        for a in soup.find_all("a"):
            title = clean(a.get_text() or "")
            href = a.get("href") or ""
            if len(title) < 8:
                continue

            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = src["base"].rstrip("/") + href
            elif not href.startswith("http"):
                continue

            # 只要文章页，挡掉导航/页脚/推荐位
            if art_re:
                m = art_re.search(href)
                if not m:
                    continue
                if date_group and m.group(date_group) not in ok_months:
                    continue

            k = (title, href)
            if k in seen_local:
                continue
            seen_local.add(k)
            out.append({"title": title, "link": href, "summary": "", "src": name})
            if len(out) >= limit:
                break

        SRC_STATUS.append({"name": name, "ok": True, "n": len(out), "err": ""})
        log(f"✓ {name}: {len(out)} 条文章链接")
        return out

    except Exception as ex:
        SRC_STATUS.append({"name": name, "ok": False, "n": 0, "err": type(ex).__name__})
        log(f"✗ {name}: {type(ex).__name__} — {ex}")
        return []


# ======================================================================
# 可选：模型精炼层
# ======================================================================

def refine_with_model(deals: List[Dict]) -> List[Dict]:
    """
    关键词规则分不清"获评专精特新"和"获红杉领投"。如果配了 ANTHROPIC_API_KEY，
    让模型把候选压缩成真正的融资事件并抽取四元组。任何失败都降级回原列表。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not deals:
        return deals

    try:
        listing = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(deals, 1))
        prompt = (
            "以下是从中文科技媒体标题里用关键词粗筛出的候选，其中混有非融资新闻。\n"
            "请只保留真正的『股权融资/并购』事件，剔除获奖、获批、产品发布、"
            "签约合作、IPO 上市等非融资内容。\n\n"
            f"{listing}\n\n"
            "只输出 JSON 数组，不要任何解释或 markdown 代码块。每个元素："
            '{"idx": 原序号, "company": "公司名", "round": "轮次或未知", '
            '"amount": "金额或未披露", "investors": "投资方或未知"}'
        )
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        parsed = json.loads(text)

        refined = []
        for row in parsed:
            i = int(row.get("idx", 0)) - 1
            if 0 <= i < len(deals):
                d = dict(deals[i])
                d["company"] = row.get("company", "")
                d["round"] = row.get("round", "")
                d["investors"] = row.get("investors", "")
                if row.get("amount") and row["amount"] != "未披露":
                    d["amount_hint"] = row["amount"]
                refined.append(d)

        if refined:
            log(f"模型精炼：{len(deals)} → {len(refined)} 条")
            return refined
        return deals

    except Exception as ex:
        log(f"模型精炼失败，降级为规则结果：{type(ex).__name__} — {ex}")
        return deals


# ======================================================================
# 推送
# ======================================================================

def post_to_serverchan(sendkey: str, title: str, body: str) -> None:
    # Server酱 desp 有长度上限，留点余量
    if len(body) > 30000:
        body = body[:30000] + "\n\n> ⚠ 内容过长已截断"
    r = requests.post(
        f"https://sctapi.ftqq.com/{sendkey}.send",
        data={"title": title[:100], "desp": body},
        timeout=30,
    )
    r.raise_for_status()


# ======================================================================
# 主逻辑
# ======================================================================

def build_briefing(seen: Dict[str, str]) -> Tuple[str, str, List[str]]:
    now_cn = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    today = now_cn.strftime("%Y-%m-%d")

    # ---- 1) 抓取 ----
    pool_cn: List[Dict] = []
    for name, url in CHINA_RSS_FEEDS:
        pool_cn.extend(fetch_rss(name, url))
    for src in CHINA_HTML_SOURCES:
        pool_cn.extend(fetch_html_links(src))

    pool_os: List[Dict] = []
    for name, url in OVERSEAS_FEEDS:
        pool_os.extend(fetch_rss(name, url, limit=60))

    # ---- 2) 分类过滤（基金优先，修正原版 elif 错分）----
    deals, funds, new_keys = [], [], []
    for it in pool_cn:
        title = it.get("title", "")
        if not title:
            continue
        k = norm_key(title)
        if k in seen:
            continue

        blob = f"{title} {it.get('summary', '')}"
        if is_fund_news(title):
            funds.append({
                "title": title, "link": it.get("link", ""),
                "amount_hint": extract_amount(blob), "src": it.get("src", ""),
                "_k": k,
            })
        elif is_true_deal(title):
            deals.append({
                "title": title, "link": it.get("link", ""),
                "sector": detect_sector(blob), "amount_hint": extract_amount(blob),
                "src": it.get("src", ""), "_k": k,
            })

    deals = list({d["_k"]: d for d in deals}.values())[:MAX_DEALS]
    funds = list({f["_k"]: f for f in funds}.values())[:MAX_FUNDS]

    overseas = []
    for it in pool_os:
        title = it.get("title", "")
        if not title:
            continue
        k = norm_key(title)
        if k in seen:
            continue
        blob = (title + " " + it.get("summary", "")).lower()
        if has_any(blob, OVERSEAS_HIT_WORDS) or AMOUNT_EN_RE.search(blob):
            overseas.append({
                "title": title, "link": it.get("link", ""),
                "amount_hint": extract_amount(title + " " + it.get("summary", "")),
                "src": it.get("src", ""), "_k": k,
            })
    overseas = list({o["_k"]: o for o in overseas}.values())[:MAX_OVERSEAS]

    # ---- 3) 可选精炼 ----
    deals = refine_with_model(deals)

    for x in deals + funds + overseas:
        new_keys.append(x["_k"])

    # ---- 4) 组装 ----
    disclosed = sum(1 for d in deals if d["amount_hint"] != "未披露")
    src_ok = sum(1 for s in SRC_STATUS if s["ok"])
    src_all = len(SRC_STATUS)

    md = [f"# {today} 股权投融资 Daily Briefing\n"]

    md.append(f"## 🇨🇳 中国融资动态（{len(deals)}）")
    if deals:
        for i, d in enumerate(deals, 1):
            md.append(f"{i}. **[{d['title']}]({d['link']})**")
            line = f"   - 赛道：{d['sector']}｜金额：{d['amount_hint']}"
            if d.get("round"):
                line += f"｜轮次：{d['round']}"
            if d.get("investors"):
                line += f"｜投资方：{d['investors']}"
            md.append(line + f"｜来源：{d['src']}")
    else:
        md.append("- 窗口内无新增融资条目。")

    md.append("\n## 📊 统计")
    md.append(f"- 融资条目：**{len(deals)}**（含金额 {disclosed}｜未披露 {len(deals) - disclosed}）")
    md.append(f"- 基金动态：**{len(funds)}**｜海外：**{len(overseas)}**")
    md.append(f"- 信源健康：**{src_ok}/{src_all}**")

    md.append(f"\n## 🏦 VC/PE 基金动态（{len(funds)}）")
    if funds:
        for i, f in enumerate(funds, 1):
            md.append(f"{i}. **[{f['title']}]({f['link']})**")
            md.append(f"   - 规模线索：{f['amount_hint']}｜来源：{f['src']}")
    else:
        md.append("- 窗口内无募资/设立/备案类条目。")

    md.append(f"\n## 🌍 海外对比（{len(overseas)}）")
    if overseas:
        for o in overseas:
            md.append(f"- **[{o['title']}]({o['link']})** — {o['amount_hint']}｜{o['src']}")
    else:
        md.append("- 窗口内无海外融资条目。")

    bad = [s for s in SRC_STATUS if not s["ok"]]
    if bad:
        md.append("\n## ⚠️ 异常信源")
        for s in bad:
            md.append(f"- {s['name']}：{s['err']}")

    md.append(f"\n---\n窗口：{MAX_AGE_HOURS}h｜生成于 {now_cn.strftime('%Y-%m-%d %H:%M')} (UTC+8)")

    title = f"{today} 投融资晨报 | {len(deals)}条 | 源 {src_ok}/{src_all}"
    return title, "\n".join(md), new_keys


def main() -> int:
    sendkey = os.environ.get("SENDKEY")
    if not sendkey:
        print("FATAL: 环境变量 SENDKEY 未配置", file=sys.stderr)
        return 2

    now_cn = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    today = now_cn.strftime("%Y-%m-%d")

    try:
        seen = load_seen()
        log(f"已有去重记录 {len(seen)} 条")

        title, body, new_keys = build_briefing(seen)

        # 落盘（供 Actions 上传 artifact）
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{today}.md").write_text(body, encoding="utf-8")
        (OUT_DIR / "latest.md").write_text(body, encoding="utf-8")

        # 先推送，成功后再落 seen —— 顺序反了的话推送失败就永久丢了这批
        post_to_serverchan(sendkey, title, body)
        log(f"推送成功：{title}")

        for k in new_keys:
            seen[k] = today
        save_seen(seen)
        return 0

    except Exception as ex:
        log(f"FATAL: {type(ex).__name__} — {ex}")
        try:
            post_to_serverchan(
                sendkey,
                f"{today} 晨报生成失败",
                f"# {today} 晨报失败\n\n"
                f"- 错误类型：`{type(ex).__name__}`\n"
                f"- 错误信息：{ex}\n\n"
                f"## 信源状态\n" +
                ("\n".join(
                    f"- {'✓' if s['ok'] else '✗'} {s['name']}：{s['n'] if s['ok'] else s['err']}"
                    for s in SRC_STATUS
                ) or "- （尚未开始抓取）"),
            )
        except Exception as ex2:
            log(f"失败告警也推送不出去：{type(ex2).__name__} — {ex2}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
