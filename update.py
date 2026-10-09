#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
肠癌肝转移（CRLM）文献周报更新脚本
====================================
用法:
    python update.py               # 抓取过去 7 天（config.yaml: window_days）
    python update.py --days 28     # 指定时间窗（首次运行建议 28 天回填）

数据源: PubMed / bioRxiv+medRxiv / ClinicalTrials.gov / ChiCTR(best-effort)
中文摘要: 优先大模型 API（环境变量 LLM_API_KEY, 可选 LLM_BASE_URL / LLM_MODEL），
          未配置时自动回退为英文摘要片段 + 关键词规则分类。
输出: site/index.html（单文件静态网站）
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests
import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
SITE_DIR = os.path.join(BASE, "site")
TEMPLATE_DIR = os.path.join(BASE, "template")

UA = {"User-Agent": "Mozilla/5.0 (CRLM-weekly; research digest)"}
TIMEOUT = 30

# ---------------------------------------------------------------- utilities

def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_jsonl(path):
    items = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        items[rec["uid"]] = rec
                    except (json.JSONDecodeError, KeyError):
                        continue
    return items


def save_jsonl(path, items):
    # 整体重写，避免对 S3 类挂载做增量 append
    with open(path, "w", encoding="utf-8") as f:
        for rec in items.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def http_get(url, **kw):
    kw.setdefault("headers", UA)
    kw.setdefault("timeout", TIMEOUT)
    # 429/5xx 指数退避重试（PubMed 等公共 API 限流）
    for attempt in range(4):
        try:
            resp = requests.get(url, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} Client/Server Error", response=resp)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if (code == 429 or code >= 500) and attempt < 3:
                wait = 5 * (attempt + 1)
                log(f"HTTP {code}，{wait}s 后重试（{attempt + 1}/3）: {url[:80]}")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def iso_days_ago(n):
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


def title_case_journal(name):
    """将 PubMed 的句首大写期刊名转为标题式大写（保留缩写与介词规则）"""
    if not name:
        return name
    small = {"of", "the", "and", "in", "on", "for", "with", "a", "an", "to", "de", "la", "et"}
    words = name.split()
    out = []
    for i, w in enumerate(words):
        if w.lower() in small and i not in (0, len(words) - 1):
            out.append(w.lower())
        elif w.isupper() or any(c.isdigit() for c in w):
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)

# ---------------------------------------------------------------- PubMed

def fetch_pubmed(query, days, max_n):
    """返回 list[dict]: uid/title/authors/journal/issn/date/abstract/doi/citations"""
    log("PubMed: 检索中 ...")
    r = http_get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={
            "db": "pubmed", "term": query, "retmode": "json",
            "datetype": "edat", "reldate": days, "retmax": max_n, "sort": "date",
        },
    )
    pmids = r.json()["esearchresult"].get("idlist", [])
    log(f"PubMed: 命中 {len(pmids)} 篇")
    if not pmids:
        return []

    papers = []
    for i in range(0, len(pmids), 200):
        chunk = pmids[i:i + 200]
        r = http_get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"},
        )
        root = ET.fromstring(r.content)
        for art in root.iter("PubmedArticle"):
            p = _parse_pubmed_article(art)
            if p:
                papers.append(p)
        time.sleep(1.5)
    log(f"PubMed: 解析 {len(papers)} 篇")
    return papers


def _parse_pubmed_article(art):
    def txt(node, path, default=""):
        el = node.find(path)
        return (el.text or default).strip() if el is not None and el.text else default

    pmid = txt(art, ".//MedlineCitation/PMID")
    if not pmid:
        return None
    title = re.sub(r"\s+", " ", txt(art, ".//MedlineCitation/Article/ArticleTitle"))
    abstract_parts = [
        re.sub(r"\s+", " ", (ab.text or "").strip())
        for ab in art.findall(".//MedlineCitation/Article/Abstract/AbstractText")
        if ab.text
    ]
    abstract = " ".join(abstract_parts)
    journal = title_case_journal(txt(art, ".//MedlineCitation/Article/Journal/Title"))
    issn = txt(art, ".//MedlineCitation/Article/Journal/ISSN")
    year = txt(art, ".//JournalIssue/PubDate/Year") or txt(art, ".//JournalIssue/PubDate/MedlineDate")[:4]
    month = txt(art, ".//JournalIssue/PubDate/Month")
    authors = []
    for au in art.findall(".//MedlineCitation/Article/AuthorList/Author")[:6]:
        ln = (au.findtext("LastName") or "").strip()
        ini = (au.findtext("Initials") or "").strip()
        if ln:
            authors.append(f"{ln} {ini}".strip())
        elif au.findtext("CollectiveName"):
            authors.append(au.findtext("CollectiveName").strip())
    doi = ""
    for aid in art.findall(".//ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = (aid.text or "").strip()
    date = f"{year}-{month}" if year and month else (year or "")
    date = normalize_date(date)
    # 机构地址 → 是否中国（用于指南/共识的中国标识）
    affs = " ".join((a.text or "") for a in art.findall(".//Affiliation"))
    china = "china" in affs.lower()
    return {
        "uid": f"pmid:{pmid}",
        "source": "journal",
        "title": title,
        "authors": ", ".join(authors) + (", et al." if len(art.findall(".//MedlineCitation/Article/AuthorList/Author")) > 6 else ""),
        "journal": journal,
        "issn": issn,
        "date": date,
        "abstract": abstract,
        "doi": doi,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "citations": None,
        "china": china,
    }


def fetch_citations(papers):
    """Europe PMC 批量查询被引数（仅 PMID 来源）"""
    pmids = [p["uid"].split(":", 1)[1] for p in papers if p["uid"].startswith("pmid:")]
    if not pmids:
        return
    log(f"Europe PMC: 查询 {len(pmids)} 篇被引数 ...")
    cmap = {}
    for i in range(0, len(pmids), 25):
        chunk = pmids[i:i + 25]
        q = " OR ".join(f"EXT_ID:{x}" for x in chunk)
        try:
            r = http_get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={"query": q, "format": "json", "pageSize": 25, "resultType": "core"},
            )
            for res in r.json().get("resultList", {}).get("result", []):
                cmap[res["id"]] = res.get("citedByCount")
        except Exception as e:
            log(f"Europe PMC 警告: {e}")
        time.sleep(0.4)
    for p in papers:
        if p["uid"].startswith("pmid:"):
            p["citations"] = cmap.get(p["uid"].split(":", 1)[1])
    log(f"Europe PMC: 命中被引数 {sum(1 for p in papers if p['citations'] is not None)} 篇")

# ---------------------------------------------------------------- 预印本

def fetch_preprints(cfg, days):
    """返回 (CRLM相关预印本列表, 原始拉取总数)"""
    log("bioRxiv/medRxiv: 拉取中 ...")
    onco_kw = [k.lower() for k in cfg["preprint_onco_keywords"]]
    out, raw_total = [], 0
    for server in ("medrxiv", "biorxiv"):
        cursor = 0
        while True:
            try:
                r = http_get(f"https://api.biorxiv.org/details/{server}/{iso_days_ago(days)}/{iso_days_ago(0)}/{cursor}", timeout=90)
                payload = r.json().get("collection", [])
            except Exception as e:
                log(f"{server} 警告: {e}")
                break
            if not payload:
                break
            raw_total += len(payload)
            for it in payload:
                text = f"{it.get('title','')} {it.get('abstract','')}".lower()
                if any(k in text for k in onco_kw):
                    doi = it.get("doi", "")
                    out.append({
                        "uid": f"doi:{doi}" if doi else f"{server}:{it.get('doi','')}",
                        "source": "preprint",
                        "server": "medRxiv" if server == "medrxiv" else "bioRxiv",
                        "title": re.sub(r"\s+", " ", it.get("title", "")),
                        "authors": it.get("authors", ""),
                        "journal": ("medRxiv" if server == "medrxiv" else "bioRxiv") + " (预印本)",
                        "issn": "",
                        "date": (it.get("date") or "")[:10],
                        "abstract": re.sub(r"\s+", " ", it.get("abstract", "")),
                        "doi": doi,
                        "url": f"https://doi.org/{doi}" if doi else "",
                        "citations": None,
                    })
            total = int(r.json().get("messages", [{}])[0].get("total", 0) or 0)
            cursor += 30
            if cursor >= total:
                break
            time.sleep(0.5)
    log(f"预印本: 原始 {raw_total} 条, 肠癌相关 {len(out)} 篇")
    return out, raw_total

# ---------------------------------------------------------------- ClinicalTrials.gov

CTG_STATUS_ZH = {
    "RECRUITING": "招募中", "ACTIVE_NOT_RECRUITING": "进行中·不再招募",
    "COMPLETED": "已完成", "ENROLLING_BY_INVITATION": "邀请招募",
    "NOT_YET_RECRUITING": "尚未招募", "WITHDRAWN": "已撤回",
    "SUSPENDED": "暂停", "TERMINATED": "终止", "UNKNOWN": "未知",
}
CTG_PHASE_ZH = {
    "EARLY_PHASE1": "早期 I 期", "PHASE1": "I 期", "PHASE2": "II 期",
    "PHASE3": "III 期", "PHASE4": "IV 期", "NA": "N/A",
}


def fetch_clinicaltrials(condition, days):
    log("ClinicalTrials.gov: 检索中 ...")
    start, end = iso_days_ago(days), iso_days_ago(0)
    out, token = [], None
    while True:
        params = {
            "query.cond": condition,
            "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{start},{end}]",
            "pageSize": 100,
        }
        if token:
            params["pageToken"] = token
        try:
            r = http_get("https://clinicaltrials.gov/api/v2/studies", params=params)
            data = r.json()
        except Exception as e:
            log(f"ClinicalTrials.gov 警告: {e}")
            break
        for st in data.get("studies", []):
            proto = st.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_m = proto.get("statusModule", {})
            desc = proto.get("descriptionModule", {})
            sponsor = proto.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
            arms = proto.get("armsInterventionsModule", {})
            interventions = ", ".join(
                (i.get("name") or "").strip() for i in arms.get("interventions", []) if i.get("name")
            )
            enrollment = proto.get("designModule", {}).get("enrollmentInfo", {}).get("count")
            created = (status_m.get("recordCreationDate") or "")[:10]
            nct = ident.get("nctId", "")
            if not nct:
                continue
            raw_status = status_m.get("overallStatus", "UNKNOWN").upper().replace(" ", "_")
            phases = proto.get("designModule", {}).get("phases", [])
            phase_zh = " / ".join(CTG_PHASE_ZH.get(ph, ph) for ph in phases) if phases else "观察性"
            out.append({
                "uid": f"nct:{nct}",
                "registry": "ClinicalTrials.gov",
                "regid": nct,
                "title": ident.get("briefTitle", ""),
                "cn_title": "",
                "interventions": interventions,
                "enrollment": enrollment,
                "status": CTG_STATUS_ZH.get(raw_status, raw_status),
                "phase": phase_zh,
                "sponsor": sponsor,
                "brief": re.sub(r"\s+", " ", desc.get("briefSummary", ""))[:400],
                "date": (status_m.get("lastUpdatePostDateStruct", {}).get("date") or "")[:10],
                "created": created,
                "url": f"https://clinicaltrials.gov/study/{nct}",
            })
        token = data.get("nextPageToken")
        if not token:
            break
        time.sleep(0.5)
    log(f"ClinicalTrials.gov: {len(out)} 项")
    return out

# ---------------------------------------------------------------- ChiCTR (best-effort)

def fetch_chictr(keyword):
    """ChiCTR 无稳定公开 API，best-effort 抓取搜索页；失败则跳过并告警。"""
    log("ChiCTR: 尝试抓取（best-effort）...")
    try:
        r = http_get(
            "https://www.chictr.org.cn/searchproj.html",
            params={"keyword": keyword},
        )
        # 站点为动态渲染，静态抓取通常拿不到结果行
        if "ChiCTR" not in r.text or len(r.text) < 2000:
            raise ValueError("页面内容异常（动态渲染，无法静态解析）")
        rows = re.findall(r"ChiCTR\d{8}[-\w]*", r.text)
        rows = sorted(set(rows))
        log(f"ChiCTR: 解析到 {len(rows)} 个注册号（静态解析，信息有限）")
        return [{
            "uid": f"chictr:{rid}",
            "registry": "ChiCTR",
            "regid": rid,
            "title": f"ChiCTR 注册号 {rid}（详情见注册中心）",
            "status": "详见注册中心",
            "phase": "N/A",
            "sponsor": "",
            "brief": "",
            "date": "",
            "url": f"https://www.chictr.org.cn/showproj.html?proj={rid}",
        } for rid in rows]
    except Exception as e:
        log(f"ChiCTR 警告: {e} —— 本轮跳过 ChiCTR，不影响其他数据源")
        return []

# ---------------------------------------------------------------- 摘要与分类

CATEGORY_RULES = [
    ("外科手术", ["surgery", "surgical", "hepatectomy", "resection", "laparoscop", "posterior approach", "alpps", "stage"]),
    ("系统治疗", ["chemotherapy", "bevacizumab", "cetuximab", "immunotherapy", "pembrolizumab", "nivolumab", "folfiri", "folfox", "trifluridine", "systemic therapy", "neoadjuvant", "adjuvant", "tislelizumab", "regorafenib", "trastuzumab"]),
    ("局部治疗", ["ablation", "radioembolization", "tace", "transarterial", "radiofrequency", "stereotactic", "sbrt", "irreversible electroporation", "hepatic artery infusion", "hai"]),
    ("生物标志物", ["biomarker", "liquid biopsy", "ctdna", "circulating tumor", "exosome", "signature", "gene expression", "molecular"]),
    ("预后模型", ["prognosis", "prognostic", "nomogram", "predictive model", "risk score", "survival prediction", "score"]),
    ("转化研究", ["mechanism", "pathway", "organoid", "xenograft", "mouse model", "single-cell", "single cell", "spatial transcriptom", "crispr", "tumor microenvironment", "tme", "immune microenvironment", "gut microbiota", "exosome"]),
]


def rule_category(text):
    t = text.lower()
    for cat, kws in CATEGORY_RULES:
        if any(k in t for k in kws):
            return cat
    return "综合及其他"


def rule_summary(paper):
    """无 LLM 时的回退摘要：英文摘要首句（截断）"""
    ab = paper.get("abstract") or paper["title"]
    ab = re.sub(r"\s+", " ", ab).strip()
    m = re.match(r"(.+?[.!?])\s", ab)
    first = m.group(1) if m else ab
    return first[:180] + ("…" if len(first) > 180 else "")


# 研究类型规则（按顺序匹配，先特异后宽泛）
STUDY_TYPE_RULES = [
    ("指南/共识", ["guideline", "consensus", "recommendation"]),
    ("系统综述/Meta", ["systematic review", "meta-analysis", "meta analysis", "pooled analysis"]),
    ("RCT", ["randomized", "randomised", "phase 3", "phase iii", "rct"]),
    ("病例系列/个案", ["case report", "case series", "case-control"]),
    ("方法学/AI", ["artificial intelligence", "deep learning", "machine learning", "nomogram", "prediction model", "radiomics model"]),
    ("影像与核医学", ["pet/ct", "pet-ct", "radiomic", "mri", "imaging", "hepatobiliary phase", "gadoxetic", "ultrasound"]),
    ("多组学与生信", ["single-cell", "single cell", "scrna", "spatial transcriptom", "multi-omics", "bulk rna", "tcga", "bioinformatic", "gene expression profiling", "wnt", "genomic analysis"]),
    ("基础机制", ["mechanism", "in vivo", "in vitro", "xenograft", "organoid", "mouse model", "mice", "crispr", "cell lines", "pathway", "tumor microenvironment", "tme", "gut microbiota"]),
    ("前瞻/干预性(非随机)", ["prospective", "phase 1", "phase 2", "phase i", "phase ii", "interventional", "pilot study"]),
    ("回顾性队列/病例对照", ["retrospective", "cohort", "seer", "ncdb", "registry", "database"]),
]
STUDY_TYPES = [t for t, _ in STUDY_TYPE_RULES] + ["其他"]


def rule_study_type(text):
    t = text.lower()
    for typ, kws in STUDY_TYPE_RULES:
        if any(k in t for k in kws):
            return typ
    return "其他"


def grade_of(if_value):
    """按 IF 分级：S ≥25，A ≥10，B ≥5，C <5；无 IF 返回空"""
    if if_value is None:
        return ""
    if if_value >= 25:
        return "S"
    if if_value >= 10:
        return "A"
    if if_value >= 5:
        return "B"
    return "C"


def _clamp_score(v, max_v):
    """LLM 分数钳位到 [0, max_v]"""
    try:
        return max(0, min(int(round(float(v))), max_v))
    except (TypeError, ValueError):
        return max_v // 3


GRADE_DEFS = {
    "S": "顶刊 IF≥15 或可能改变临床实践",
    "A": "高质量前瞻性/随机研究",
    "B": "单中心回顾性或机制研究",
    "C": "其他纳入文献",
}

# 规则回退：研究类型 → 等级
TYPE_GRADE = {
    "RCT": "A", "前瞻/干预性(非随机)": "A",
    "回顾性队列/病例对照": "B", "基础机制": "B", "多组学与生信": "B", "系统综述/Meta": "B",
}


def assign_grade(p):
    """等级：规则层 + LLM 实践改变潜力覆盖，附明细"""
    if p.get("source") == "preprint":
        p["grade"], p["grade_rationale"] = "", "预印本未经同行评审，不参与分级"
        return p["grade"]
    if_v = p.get("if_value")
    rule = "S" if (if_v is not None and if_v >= 15) else TYPE_GRADE.get(p.get("study_type", ""), "C")
    if p.get("practice_changing"):
        rule = "S"
    p["grade"] = rule
    if not p.get("grade_rationale"):
        p["grade_rationale"] = (
            f"IF {if_v}（≥15 顶刊）" if if_v is not None and if_v >= 15
            else f"研究类型「{p.get('study_type','其他')}」对应 {rule} 级"
        ) + ("；LLM 判定可能改变临床实践" if p.get("practice_changing") else "")
    return rule


def impact_score(if_value):
    """影响力 0-20：IF 线性映射，IF≥30 封顶"""
    if if_value is None:
        return 5
    try:
        if_value = float(if_value)
    except (TypeError, ValueError):
        return 5
    return max(0, min(20, int(round(if_value / 30 * 20))))


def total_score(p):
    return (p.get("design", 0) + p.get("validation", 0) + p.get("mechanism", 0)
            + p.get("impact", 0) + p.get("fit", 0))


def rule_scores(p):
    """无 LLM 时的规则回退评分"""
    st = p.get("study_type", "其他")
    p["design"] = {"RCT": 28, "前瞻/干预性(非随机)": 24}.get(st, 15 if st in ("回顾性队列/病例对照", "系统综述/Meta") else 12)
    p["validation"] = 12 if st in ("RCT", "前瞻/干预性(非随机)", "系统综述/Meta") else 7
    p["mechanism"] = 11 if st in ("基础机制", "多组学与生信") else 7
    p["fit"] = 14
    p["impact"] = impact_score(p.get("if_value"))


def load_env():
    """从项目目录 .env 文件加载环境变量（不覆盖已存在的环境变量）"""
    env_path = os.path.join(BASE, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


class LLMClient:
    def __init__(self, cfg):
        load_env()
        self.key = os.environ.get("LLM_API_KEY", "")
        self.base = os.environ.get("LLM_BASE_URL", cfg["llm_base_url_default"]).rstrip("/")
        self.model = os.environ.get("LLM_MODEL", cfg["llm_model_default"])
        self.ok = bool(self.key)

    def summarize(self, papers, categories):
        """单次调用生成全部字段：返回 {uid: {...}}；失败返回空 dict"""
        results = {}
        url = f"{self.base}/chat/completions"
        cats = " / ".join(categories)
        types = " / ".join(STUDY_TYPES)
        for p in papers:
            text = f"Title: {p['title']}\nAbstract: {(p.get('abstract') or '')[:3000]}"
            prompt = (
                "你是结直肠癌（含肝转移）领域的资深研究者，正在为课题组撰写文献周报。请阅读以下文献，输出 JSON：\n"
                '{\n'
                '  "cn_title": "中文标题（简洁学术表达，不超过40字）",\n'
                '  "insight": "一句话总结：这篇文献的意义 + 对肠癌/肝转移研究的启示（不超过80字）",\n'
                f'  "category": "从以下类别中选一个最贴切的: {cats}",\n'
                f'  "study_type": "从以下类型中选一个最贴切的: {types}",\n'
                '  "practice_changing": true或false（是否可能改变临床实践）,\n'
                '  "grade_rationale": "等级判定理由（一句话，说明依据IF、研究设计或实践改变潜力）",\n'
                '  "design": 0到30的整数（研究设计质量：RCT/前瞻/多中心高分，个案/回顾低分）,\n'
                '  "validation": 0到15的整数（验证程度：外部验证/大样本高分，无验证低分）,\n'
                '  "mechanism": 0到15的整数（机制深度：深层机制解析高分，描述性研究低分）,\n'
                '  "fit": 0到20的整数（肠癌领域重要性与契合度：肝转移直接相关或肠癌领域重大进展（大型临床研究、指南共识、实践改变）高分，外围低分）\n'
                '}\n\n' + text
            )
            try:
                r = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={"model": self.model, "temperature": 0.2,
                          "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]},
                    timeout=90,
                )
                r.raise_for_status()
                obj = json.loads(r.json()["choices"][0]["message"]["content"])
                summary = str(obj.get("insight", "")).strip()
                cat = str(obj.get("category", "")).strip()
                stype = str(obj.get("study_type", "")).strip()
                if cat not in categories:
                    cat = rule_category(p["title"] + " " + p.get("abstract", ""))
                if stype not in STUDY_TYPES:
                    stype = rule_study_type(p["title"] + " " + p.get("abstract", ""))
                if summary:
                    results[p["uid"]] = {
                        "cn_title": str(obj.get("cn_title", "")).strip(),
                        "insight": summary,
                        "category": cat,
                        "study_type": stype,
                        "practice_changing": bool(obj.get("practice_changing", False)),
                        "grade_rationale": str(obj.get("grade_rationale", "")).strip(),
                        "design": _clamp_score(obj.get("design"), 30),
                        "validation": _clamp_score(obj.get("validation"), 15),
                        "mechanism": _clamp_score(obj.get("mechanism"), 15),
                        "fit": _clamp_score(obj.get("fit"), 20),
                    }
            except Exception as e:
                log(f"LLM 警告（{p['uid']}）: {e}，该条回退规则模式")
            time.sleep(0.3)
        return results

    def reading(self, papers):
        """Top10 精读：返回 {uid: {background, results, significance}}"""
        out = {}
        for p in papers:
            text = f"Title: {p['title']}\nAbstract: {(p.get('abstract') or '')[:3000]}"
            prompt = (
                "你是结直肠癌（含肝转移）领域资深研究者。请为以下文献撰写中文精读卡片，输出 JSON：\n"
                '{"background": "研究背景（1-2句）", "results": "核心结果（2-3句，含关键数据）", '
                '"significance": "临床意义与对课题的启示（1-2句）"}\n\n' + text
            )
            try:
                r = requests.post(
                    f"{self.base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={"model": self.model, "temperature": 0.2,
                          "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]},
                    timeout=90,
                )
                r.raise_for_status()
                obj = json.loads(r.json()["choices"][0]["message"]["content"])
                out[p["uid"]] = {k: str(obj.get(k, "")).strip() for k in ("background", "results", "significance")}
            except Exception as e:
                log(f"LLM 精读警告（{p['uid']}）: {e}")
            time.sleep(0.3)
        return out

    def hotspots(self, papers):
        """生成本周热点与趋势：返回 list[str]（每条一个主题），失败返回 []"""
        corpus = []
        for i, p in enumerate(papers[:40], 1):
            corpus.append(
                f"[{i}] 期刊: {p.get('journal','')} | 标题: {p['title']} | "
                f"摘要: {(p.get('abstract') or '')[:600]}"
            )
        prompt = (
            "你是结直肠癌（含肝转移）领域的资深研究者。以下是本周新增文献的清单，"
            "请撰写「本周热点与趋势」编辑综述（肝转移为最重要亚方向，兼顾肠癌整体重要进展）：\n"
            "请撰写「本周热点与趋势」编辑综述：\n"
            "- 3~6 条，每条聚焦一个主题方向（如治疗策略、生物标志物、机制发现、手术与局部治疗等）\n"
            "- 每条 2~4 句，综合相关文献，注明期刊名与关键数据（样本量、HR、P 值等），"
            "语言风格参照专业学术周报，直接陈述结论\n"
            '- 输出 JSON: {"items": ["第1条...", "第2条...", ...]}\n\n' + "\n".join(corpus)
        )
        try:
            r = requests.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model, "temperature": 0.3,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120,
            )
            r.raise_for_status()
            obj = json.loads(r.json()["choices"][0]["message"]["content"])
            items = [str(x).strip() for x in obj.get("items", []) if str(x).strip()]
            log(f"LLM 热点综述: 生成 {len(items)} 条")
            return items
        except Exception as e:
            log(f"LLM 热点综述失败: {e}，回退高影响力文献列表")
            return []

    def translate_titles(self, trials):
        """批量翻译临床试验题目为中文：返回 {uid: cn_title}"""
        if not trials:
            return {}
        lines = [f"[{i}] {t['title']}" for i, t in enumerate(trials[:30], 1)]
        prompt = (
            "请将以下临床试验题目翻译为简洁的中文学术表达，保留药物名与编号的规范写法。\n"
            '输出 JSON: {"translations": {"1": "中文题目", "2": "...", ...}}\n\n' + "\n".join(lines)
        )
        try:
            r = requests.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model, "temperature": 0.2,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120,
            )
            r.raise_for_status()
            obj = json.loads(r.json()["choices"][0]["message"]["content"])
            trans = obj.get("translations", {})
            out = {}
            for i, t in enumerate(trials[:30], 1):
                cn = str(trans.get(str(i), "")).strip()
                if cn:
                    out[t["uid"]] = cn
            log(f"LLM 试验题目翻译: {len(out)}/{len(trials[:30])}")
            return out
        except Exception as e:
            log(f"LLM 试验题目翻译失败: {e}")
            return {}

# ---------------------------------------------------------------- 指标匹配

def apply_metrics(papers, if_map):
    n = 0
    for p in papers:
        rec = None
        issn = (p.get("issn") or "").strip().lower()
        if issn:
            rec = if_map["journals"].get("issn:" + issn)
        if rec is None:
            name = re.sub(r"\s+", " ", (p.get("journal") or "")).strip().lower()
            rec = if_map["journals"].get("name:" + name)
        if rec:
            try:
                p["if_value"] = float(rec["if"])
            except (TypeError, ValueError):
                p["if_value"] = None
            p["cas"] = rec["cas"].split(" ")[0] if rec.get("cas") else ""
            p["top"] = rec.get("top") == "是"
            n += 1
        else:
            p["if_value"] = None
            p["cas"] = ""
            p["top"] = False
    return n

# ---------------------------------------------------------------- 主流程

MONTH_MAP = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
             "jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12"}


def normalize_date(date_str):
    """归一化 PubMed 日期：2026-Sep / 2026 September → 2026-09；ISO 原样返回"""
    if not date_str:
        return ""
    date_str = date_str.strip()
    if re.match(r"^\d{4}-\d{2}", date_str):
        return date_str[:10] if len(date_str) > 7 else date_str
    m = re.match(r"^(\d{4})-?([A-Za-z]{3,9})", date_str)
    if m:
        mm = MONTH_MAP.get(m.group(2)[:3].lower())
        if mm:
            return f"{m.group(1)}-{mm}"
    m = re.match(r"^(\d{4})", date_str)
    return m.group(1) if m else ""


def week_label(date_str):
    """分组标签：完整日期 → ISO 周（2026-W38）；仅年月 → 月桶（2026-09）；仅年 → 年桶"""
    d = (date_str or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", d):
        try:
            d_ = dt.date.fromisoformat(d[:10])
            return f"{d_.isocalendar()[0]}-W{d_.isocalendar()[1]:02d}"
        except (ValueError, TypeError):
            pass
    if re.match(r"^\d{4}-\d{2}", d):
        return d[:7] + "-M"
    if re.match(r"^\d{4}", d):
        return d[:4] + "-Y"
    return "日期待定"


def label_sort_key(label):
    """分组标签排序键（时间降序用）"""
    if label == "日期待定":
        return (0, 0, 0)
    m = re.match(r"^(\d{4})-W(\d{2})$", label)
    if m:
        return (int(m.group(1)), 12, int(m.group(2)))
    m = re.match(r"^(\d{4})-(\d{2})-M$", label)
    if m:
        return (int(m.group(1)), int(m.group(2)), 0)
    m = re.match(r"^(\d{4})-Y$", label)
    if m:
        return (int(m.group(1)), 0, 0)
    return (0, 0, 0)


def label_display(label):
    """分组标签 → 中文显示"""
    m = re.match(r"^(\d{4})-W(\d{2})$", label)
    if m:
        return f"{m.group(1)} 年第 {int(m.group(2))} 周"
    m = re.match(r"^(\d{4})-(\d{2})-M$", label)
    if m:
        return f"{m.group(1)} 年 {int(m.group(2))} 月"
    m = re.match(r"^(\d{4})-Y$", label)
    if m:
        return f"{m.group(1)} 年"
    return label


# ---------------------------------------------------------------- 主题标注

METS_TOPIC_KW = ["liver metastas", "hepatic metastas", "liver tropism", "hepatic tropism", "crlm"]


def topic_of(p):
    """主题标注：肝转移（重点亚方向）/ 肠癌总体"""
    text = (p.get("title", "") + " " + (p.get("abstract") or "")).lower()
    return "肝转移" if any(k in text for k in METS_TOPIC_KW) else "肠癌总体"


def enrich_papers(papers, llm, cfg, if_map):
    """LLM/规则生成中文字段 + 指标匹配 + 等级与评分（论文与指南共用）"""
    if not papers:
        return
    if llm.ok:
        log(f"LLM 模式（{llm.model}）: 生成启示与评分 ...")
        res = llm.summarize(papers, cfg["categories"])
        log(f"LLM 成功 {len(res)}/{len(papers)} 篇")
    else:
        res = {}
        log("未配置 LLM_API_KEY，使用规则回退模式")
    for p in papers:
        info = res.get(p["uid"])
        if info:
            p.update(info)
        else:
            p["cn_title"] = ""
            p["insight"] = rule_summary(p)
            p["category"] = rule_category(p["title"] + " " + p.get("abstract", ""))
            p["study_type"] = rule_study_type(p["title"] + " " + p.get("abstract", ""))
            p["practice_changing"] = False
            p["grade_rationale"] = ""
            rule_scores(p)
    n_if = apply_metrics(papers, if_map)
    log(f"期刊指标匹配: {n_if}/{len(papers)}")
    for p in papers:
        if "impact" not in p:
            p["impact"] = impact_score(p.get("if_value"))
        assign_grade(p)
        p["score"] = total_score(p)
        p["topic"] = topic_of(p)


def main():
    ap = argparse.ArgumentParser(description="肠癌肝转移文献周报更新")
    ap.add_argument("--days", type=int, default=None, help="抓取时间窗（天），默认取 config.yaml 的 window_days")
    args = ap.parse_args()

    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    days = args.days if args.days is not None else cfg["window_days"]

    with open(os.path.join(DATA_DIR, "if_map.json"), encoding="utf-8") as f:
        if_map = json.load(f)

    # 1) 抓取（记录漏斗计数）
    funnel = {"pubmed_raw": 0, "preprint_raw": 0, "preprint_kept": 0,
              "trials_raw": 0, "dedup_removed": 0, "no_abstract": 0, "included": 0}

    papers_new = fetch_pubmed(cfg["pubmed_query"], days, cfg["max_pubmed"])
    funnel["pubmed_raw"] = len(papers_new)
    preprints, preprint_raw = fetch_preprints(cfg, days)
    funnel["preprint_raw"], funnel["preprint_kept"] = preprint_raw, len(preprints)
    funnel["preprint_excluded"] = preprint_raw - len(preprints)
    papers_new += preprints
    fetch_citations(papers_new)
    trials_new = fetch_clinicaltrials(cfg["ctg_condition"], days)
    funnel["trials_raw"] = len(trials_new)
    trials_new += fetch_chictr(cfg["chictr_keyword"])

    # 指南与共识（独立数据源，独立入库）
    guidelines_new = fetch_pubmed(cfg["guideline_query"], days, 50)
    for g in guidelines_new:
        g["source"] = "guideline"
    funnel["guidelines_raw"] = len(guidelines_new)

    # 无有效摘要的文献不纳入（计入漏斗）
    with_abs = [p for p in papers_new if (p.get("abstract") or "").strip()]
    funnel["no_abstract"] = len(papers_new) - len(with_abs)
    papers_new = with_abs

    # 2) 去重合并
    papers_db = load_jsonl(os.path.join(DATA_DIR, "papers.jsonl"))
    trials_db = load_jsonl(os.path.join(DATA_DIR, "trials.jsonl"))
    guidelines_db = load_jsonl(os.path.join(DATA_DIR, "guidelines.jsonl"))
    before = len(papers_new)
    papers_new = [p for p in papers_new if p["uid"] not in papers_db]
    trials_new = [t for t in trials_new if t["uid"] not in trials_db]
    guidelines_new = [g for g in guidelines_new if g["uid"] not in guidelines_db and g["uid"] not in papers_db]
    # 同一共识不同 PMID 版本（epub/刊出版）按标题去重
    seen_gt = {re.sub(r"\W+", "", (g.get("title") or "").lower()) for g in guidelines_db.values()}
    guidelines_new = [g for g in guidelines_new
                      if (t := re.sub(r"\W+", "", (g.get("title") or "").lower())) not in seen_gt
                      and not seen_gt.add(t)]
    funnel["dedup_removed"] = before - len(papers_new)
    funnel["included"] = len(papers_new)
    log(f"去重后新增: 文献 {len(papers_new)} 篇, 临床试验 {len(trials_new)} 项, 指南/共识 {len(guidelines_new)} 篇")

    # 3) LLM 字段生成（启示/等级理由/五维评分）
    llm = LLMClient(cfg)
    enrich_papers(papers_new, llm, cfg, if_map)
    enrich_papers(guidelines_new, llm, cfg, if_map)
    for t in trials_new:
        t["category"] = "临床试验"

    # 3.5) Top10 精读 + 热点述评 + 试验题目翻译
    hotspots, readings = [], {}
    hotspots_path = os.path.join(DATA_DIR, "hotspots.json")
    if papers_new:
        latest_week = week_label(papers_new[0]["date"]) if papers_new else ""
        week_papers = [p for p in papers_new if week_label(p.get("date")) == latest_week] or papers_new
        top10 = sorted(week_papers, key=lambda p: p.get("score", 0), reverse=True)[:10]
        if llm.ok:
            log(f"LLM: Top{len(top10)} 精读 ...")
            readings = llm.reading(top10)
            log(f"LLM 精读成功 {len(readings)}/{len(top10)}")
            hotspots = llm.hotspots(top10)
            if hotspots:
                with open(hotspots_path, "w", encoding="utf-8") as f:
                    json.dump(hotspots, f, ensure_ascii=False)
            trans = llm.translate_titles(trials_new)
            for t in trials_new:
                if t["uid"] in trans:
                    t["cn_title"] = trans[t["uid"]]
        if not hotspots:
            top = sorted([p for p in week_papers if p.get("if_value")], key=lambda p: p["if_value"], reverse=True)[:5]
            hotspots = [f"《{p['journal']}》（IF {p['if_value']}）：{p['title']}" for p in top]
            if hotspots:
                log(f"热点综述（回退模式）: 列出本周 IF 最高 {len(hotspots)} 篇")
    elif os.path.exists(hotspots_path):
        # 空运行重渲染：沿用已持久化的热点述评
        with open(hotspots_path, encoding="utf-8") as f:
            hotspots = json.load(f)

    # 3.6) 精读入库（累计）
    readings_path = os.path.join(DATA_DIR, "readings.json")
    readings_db = {}
    if os.path.exists(readings_path):
        with open(readings_path, encoding="utf-8") as f:
            readings_db = json.load(f)
    readings_db.update(readings)
    with open(readings_path, "w", encoding="utf-8") as f:
        json.dump(readings_db, f, ensure_ascii=False)

    # 3.7) 漏斗持久化（仅在有新增时覆盖，避免空运行抹掉本期口径）
    funnel_path = os.path.join(DATA_DIR, "funnel.json")
    if funnel["included"] > 0:
        funnel["window_start"] = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
        funnel["window_end"] = dt.date.today().isoformat()
        with open(funnel_path, "w", encoding="utf-8") as f:
            json.dump(funnel, f, ensure_ascii=False)
    elif os.path.exists(funnel_path):
        with open(funnel_path, encoding="utf-8") as f:
            funnel = json.load(f)

    # 4) 入库
    for p in papers_new:
        papers_db[p["uid"]] = p
    for t in trials_new:
        trials_db[t["uid"]] = t
    for g in guidelines_new:
        guidelines_db[g["uid"]] = g
    save_jsonl(os.path.join(DATA_DIR, "papers.jsonl"), papers_db)
    save_jsonl(os.path.join(DATA_DIR, "trials.jsonl"), trials_db)
    save_jsonl(os.path.join(DATA_DIR, "guidelines.jsonl"), guidelines_db)
    log(f"数据库: 文献 {len(papers_db)} 篇, 临床试验 {len(trials_db)} 项, 指南/共识 {len(guidelines_db)} 篇")

    # 5) 渲染
    render(papers_db, trials_db, if_map, cfg, funnel, hotspots, guidelines_db)
    log(f"完成: {os.path.join(SITE_DIR, 'index.html')}")


def render(papers_db, trials_db, if_map, cfg, funnel, hotspots, guidelines_db):
    from jinja2 import Environment, FileSystemLoader

    papers = sorted(papers_db.values(), key=lambda p: p.get("date") or "", reverse=True)
    trials = sorted(trials_db.values(), key=lambda t: t.get("date") or "", reverse=True)
    guidelines = sorted(guidelines_db.values(), key=lambda g: g.get("date") or "", reverse=True)
    journal_papers = [p for p in papers if p.get("source") == "journal"]
    preprints = [p for p in papers if p.get("source") == "preprint"]

    # IF 排名（全部期刊论文，IF 降序）
    ranked = sorted(
        [p for p in journal_papers if p.get("if_value") is not None],
        key=lambda p: p["if_value"], reverse=True,
    )
    for i, p in enumerate(ranked, 1):
        p["rank"] = i

    # Top10：最新一周按总分排序
    readings_db = {}
    readings_path = os.path.join(DATA_DIR, "readings.json")
    if os.path.exists(readings_path):
        with open(readings_path, encoding="utf-8") as f:
            readings_db = json.load(f)
    # Top10：检索窗口内（按月精度匹配）按总分排序
    ws, we = funnel.get("window_start", "")[:7], funnel.get("window_end", "")[:7]
    window_papers = [p for p in journal_papers if ws <= (p.get("date") or "")[:7] <= we] or journal_papers
    top10 = sorted(window_papers, key=lambda p: p.get("score", 0), reverse=True)[:10]
    for p in top10:
        p["reading"] = readings_db.get(p["uid"], {})

    # 统计
    ifs = [p["if_value"] for p in journal_papers if p.get("if_value") is not None]
    grade_counts = {g: sum(1 for p in journal_papers if p.get("grade") == g) for g in "SABC"}
    stats = {
        "n_papers": len(journal_papers),
        "n_preprints": len(preprints),
        "n_journals": len({(p.get("journal") or "").lower() for p in journal_papers if p.get("journal")}),
        "max_if": max(ifs) if ifs else None,
        "avg_if": round(sum(ifs) / len(ifs), 1) if ifs else None,
        "n_no_if": sum(1 for p in journal_papers if p.get("if_value") is None),
        "n_trials": len(trials),
        "n_guidelines": len(guidelines),
        "n_new": funnel["included"],
        "grade_counts": grade_counts,
    }

    # 已发表文献按时间分组（周/月桶混合，统一排序）
    weeks = {}
    for p in journal_papers:
        weeks.setdefault(week_label(p.get("date")), []).append(p)
    week_groups = sorted(weeks.items(), key=lambda kv: label_sort_key(kv[0]), reverse=True)

    # Top10 期刊 IF 核实表（去重，取分值前12）
    if_verified = []
    seen_j = set()
    for p in ranked:
        jn = p.get("journal") or ""
        if jn.lower() not in seen_j:
            seen_j.add(jn.lower())
            if_verified.append({"journal": jn, "if": p["if_value"]})
        if len(if_verified) >= 12:
            break

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    env.filters["label_display"] = label_display
    tpl = env.get_template("index.html.j2")

    html = tpl.render(
        generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        if_year=if_map.get("impactFactorYear", ""),
        cas_year=if_map.get("partitionYear", ""),
        week_groups=week_groups,
        preprints=preprints,
        trials=trials,
        guidelines=guidelines,
        categories=cfg["categories"],
        study_types=STUDY_TYPES,
        grade_defs=GRADE_DEFS,
        stats=stats,
        funnel=funnel,
        top10=top10,
        if_verified=if_verified,
        hotspots=hotspots,
        pubmed_query=cfg["pubmed_query"].strip(),
        guideline_query=cfg["guideline_query"].strip(),
        ctg_condition=cfg["ctg_condition"],
        preprint_onco=cfg["preprint_onco_keywords"],
        preprint_mets=cfg["preprint_mets_keywords"],
        window_start=funnel.get("window_start", (dt.date.today() - dt.timedelta(days=6)).isoformat()),
        window_end=funnel.get("window_end", dt.date.today().isoformat()),
    )
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
