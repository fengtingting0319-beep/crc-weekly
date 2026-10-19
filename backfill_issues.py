#!/usr/bin/env python3
"""一次性回填历史期号：从运行日志恢复的真实漏斗数字 + 数据库按月精度取子集"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update as U

# 历史运行的真实漏斗（来源：data/*.log 与当期校验行）
HISTORY = [
    {"window_start": "2026-08-27", "window_end": "2026-09-23",
     "pubmed_raw": 77, "preprint_raw": 2258, "preprint_kept": 3, "preprint_excluded": 2255,
     "trials_raw": 18, "dedup_removed": 0, "no_abstract": 7, "included": 73},
    {"window_start": "2026-09-18", "window_end": "2026-09-24",
     "pubmed_raw": 16, "preprint_raw": 0, "preprint_kept": 0, "preprint_excluded": 0,
     "trials_raw": 9, "dedup_removed": 12, "no_abstract": 1, "included": 3},
    {"window_start": "2026-09-26", "window_end": "2026-10-02",
     "pubmed_raw": 395, "preprint_raw": 2163, "preprint_kept": 26, "preprint_excluded": 2137,
     "trials_raw": 107, "dedup_removed": 0, "no_abstract": 67, "included": 353},
    {"window_start": "2026-10-03", "window_end": "2026-10-09",
     "pubmed_raw": 327, "preprint_raw": 2230, "preprint_kept": 25, "preprint_excluded": 2205,
     "trials_raw": 140, "dedup_removed": 12, "no_abstract": 35, "included": 304},
]

def main():
    cfg = U.yaml.safe_load(open(os.path.join(U.BASE, "config.yaml"), encoding="utf-8"))
    if_map = json.load(open(os.path.join(U.DATA_DIR, "if_map.json"), encoding="utf-8"))
    papers_db = U.load_jsonl(os.path.join(U.DATA_DIR, "papers.jsonl"))
    trials_db = U.load_jsonl(os.path.join(U.DATA_DIR, "trials.jsonl"))
    guidelines_db = U.load_jsonl(os.path.join(U.DATA_DIR, "guidelines.jsonl"))

    issues_path = os.path.join(U.DATA_DIR, "issues.json")
    issues = []
    if os.path.exists(issues_path):
        issues = json.load(open(issues_path, encoding="utf-8"))

    for h in HISTORY:
        ws, we = h["window_start"], h["window_end"]
        ws7, we7 = ws[:7], we[:7]
        sub_p = [p for p in papers_db.values() if ws7 <= (p.get("date") or "")[:7] <= we7]
        sub_t = [t for t in trials_db.values() if ws7 <= (t.get("date") or "")[:7] <= we7]
        sub_g = [g for g in guidelines_db.values() if ws7 <= (g.get("date") or "")[:7] <= we7]
        # 热点回退：子集中 IF 最高 5 篇
        top_if = sorted([p for p in sub_p if p.get("if_value")], key=lambda p: p["if_value"], reverse=True)[:5]
        hotspots = [f"《{p['journal']}》（IF {p['if_value']}）：{p['title']}" for p in top_if]
        issue_id = f"{ws}__{we}"
        out_dir = os.path.join(U.SITE_DIR, "issues", issue_id)
        U.render_issue(sub_p, sub_t, sub_g, if_map, cfg, h, hotspots, out_dir,
                       home_url="../../index.html",
                       scope_note="历史回填期号，按发表月份精度归档，可能含相邻窗口文献")
        issues = [i for i in issues if i["id"] != issue_id]
        issues.append({"id": issue_id, "window_start": ws, "window_end": we,
                       "n_papers": h["included"], "n_preprints": h["preprint_kept"],
                       "n_trials": h["trials_raw"], "generated": "backfill"})
        print(f"回填 {issue_id}: 文献 {h['included']} 篇, 试验 {h['trials_raw']} 项, 页面子集 {len(sub_p)} 篇")

    issues.sort(key=lambda i: i["window_end"], reverse=True)
    with open(issues_path, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=1)
    U.render_home(issues, cfg)
    print(f"首页已渲染：{len(issues)} 期归档")

if __name__ == "__main__":
    main()
