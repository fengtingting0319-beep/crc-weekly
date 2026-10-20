#!/usr/bin/env python3
"""从最新一期周报数据生成微信朋友圈文案 + 配图卡片 + 公众号文章"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update as U

OUT = "/mnt/results/crlm_weekly/wechat"
SITE_URL = "https://fengtingting0319-beep.github.io/crc-weekly"


def load_latest():
    issues = json.load(open("data/issues.json", encoding="utf-8"))
    latest = issues[0]
    ws, we = latest["window_start"], latest["window_end"]
    papers = U.load_jsonl("data/papers.jsonl")
    ws7, we7 = ws[:7], we[:7]
    cur = [p for p in papers.values() if ws7 <= (p.get("date") or "")[:7] <= we7]
    trials = U.load_jsonl("data/trials.jsonl")
    cur_t = [t for t in trials.values() if ws7 <= (t.get("date") or "")[:7] <= we7]
    guidelines = U.load_jsonl("data/guidelines.jsonl")
    cur_g = [g for g in guidelines.values() if ws7 <= (g.get("date") or "")[:7] <= we7]
    hotspots = json.load(open("data/hotspots.json", encoding="utf-8"))
    readings = json.load(open("data/readings.json", encoding="utf-8"))
    jp = sorted([p for p in cur if p.get("source") == "journal"], key=lambda p: p.get("score", 0), reverse=True)
    preprints = [p for p in cur if p.get("source") == "preprint"]
    top10 = jp[:10]
    for p in top10:
        p["reading"] = readings.get(p["uid"], {})
    return dict(ws=ws, we=we, jp=jp, preprints=preprints, trials=cur_t, guidelines=cur_g,
                hotspots=hotspots, top10=top10, n_papers=latest["n_papers"], n_trials=latest["n_trials"])


def moments_text(d):
    """朋友圈文案：简短版 + 标准版"""
    s_grade = [p for p in d["jp"] if p.get("grade") == "S"]
    lm = [p for p in d["jp"] if p.get("topic") == "肝转移"]
    t1 = (d["top10"][0].get("cn_title") or d["top10"][0]["title"]) if d["top10"] else ""
    short = (
        f"【结直肠癌研究周报 · {d['ws']} 至 {d['we']}】\n"
        f"本周收录肠癌文献 {d['n_papers']} 篇（肝转移 {len(lm)} 篇）、临床试验 {d['n_trials']} 项，"
        f"S 级必读 {len(s_grade)} 篇。\n"
        f"本期头条：{t1}\n全文：{SITE_URL}"
    )
    std = (
        f"【结直肠癌研究周报】{d['ws']} 至 {d['we']}\n\n"
        f"本周全网检索肠癌领域文献 {d['n_papers']} 篇（其中肝转移 {len(lm)} 篇）、"
        f"临床试验更新 {d['n_trials']} 项、指南共识 {len(d['guidelines'])} 篇，"
        f"经 LLM 五维评分精选 Top10 精读。\n\n"
        f"本期看点：\n"
    )
    for i, h in enumerate(d["hotspots"][:3], 1):
        std += f"{i}. {h[:90]}…\n"
    std += f"\n完整周报（可筛选、可溯源）：{SITE_URL}"
    return short, std


def share_card(d):
    """朋友圈配图卡片 1080x1440"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Liberation Sans"]
    s_grade = [p for p in d["jp"] if p.get("grade") == "S"]
    lm = [p for p in d["jp"] if p.get("topic") == "肝转移"]

    fig, ax = plt.subplots(figsize=(10.8, 14.4), dpi=100)
    ax.set_xlim(0, 10); ax.set_ylim(0, 14.4); ax.axis("off")
    fig.patch.set_facecolor("#FAF9F3")
    ax.add_patch(plt.Rectangle((0.25, 0.25), 9.5, 13.9, fill=False, edgecolor="#0279EE", lw=3))
    ax.text(5, 13.3, "结直肠癌研究周报", ha="center", fontsize=34, fontweight="bold", color="#0f172a")
    ax.text(5, 12.55, f"{d['ws']} 至 {d['we']}", ha="center", fontsize=19, color="#0279EE", fontweight="bold")

    nums = [(str(d["n_papers"]), "收录文献（篇）"), (str(d["n_trials"]), "临床试验（项）"),
            (str(len(s_grade)), "S 级必读（篇）"), (str(len(lm)), "肝转移（篇）")]
    for i, (v, lbl) in enumerate(nums):
        x = 1.45 + (i % 2) * 4.4
        y = 10.6 if i < 2 else 8.7
        ax.text(x + 1.6, y, v, ha="center", fontsize=42, fontweight="bold", color="#0279EE")
        ax.text(x + 1.6, y - 0.75, lbl, ha="center", fontsize=15, color="#475569")

    ax.text(0.75, 7.5, "本期看点", fontsize=21, fontweight="bold", color="#0f172a")
    ax.plot([0.75, 9.25], [7.2, 7.2], color="#0279EE", lw=1.5)
    import textwrap
    y = 6.8
    for i, h in enumerate(d["hotspots"][:3], 1):
        seg = f"{i}. " + h[:76].rstrip() + ("…" if len(h) > 76 else "")
        for ln in textwrap.wrap(seg, width=40):
            ax.text(0.75, y, ln, fontsize=13, color="#1e293b")
            y -= 0.48
        y -= 0.22
    y_top1 = y + 0.1
    ax.text(0.75, y_top1, "Top1 精读", fontsize=21, fontweight="bold", color="#0f172a")
    ax.plot([0.75, 9.25], [y_top1 - 0.35, y_top1 - 0.35], color="#0279EE", lw=1.5)
    t1 = d["top10"][0] if d["top10"] else None
    if t1:
        title = (t1.get("cn_title") or t1["title"])[:40]
        ax.text(0.75, y_top1 - 0.85, f"[{t1.get('grade')} 级 · {t1.get('score')} 分] {title}", fontsize=14, color="#0f172a", fontweight="bold")
        ins = (t1.get("reading", {}).get("significance") or t1.get("insight") or "")[:80]
        yy = y_top1 - 1.4
        for ln in textwrap.wrap(ins, width=42):
            ax.text(0.75, yy, ln, fontsize=12.5, color="#475569")
            yy -= 0.45
    ax.text(5, 0.75, "结直肠癌研究周报 · 每周一自动更新", ha="center", fontsize=13, color="#64748b")
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, f"share_card_{d['we']}.png"), bbox_inches="tight", facecolor="#FAF9F3")
    plt.close(fig)


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def gzh_html(d):
    """公众号文章：全内联样式，可直接粘贴进公众号编辑器"""
    B, N, M = "#0279EE", "#0f172a", "#64748b"
    def sec_title(t):
        return (f'<section style="margin:28px 0 14px;border-left:5px solid {B};padding-left:12px">'
                f'<span style="font-size:19px;font-weight:bold;color:{N}">{t}</span></section>')
    def card(inner, bg="#ffffff", border="#e2e8f0"):
        return (f'<section style="background:{bg};border:1px solid {border};border-radius:10px;'
                f'padding:14px 16px;margin:12px 0">{inner}</section>')
    s = [f'<section style="font-family:-apple-system,PingFang SC,Microsoft YaHei,sans-serif;max-width:677px;margin:0 auto;color:{N};font-size:15px;line-height:1.8">']
    s.append(f'<section style="text-align:center;margin:10px 0 4px"><span style="font-size:22px;font-weight:bold">{esc("结直肠癌研究周报")}</span></section>')
    s.append(f'<section style="text-align:center;color:{M};font-size:13px;margin-bottom:18px">{d["ws"]} 至 {d["we"]} ｜ 肠癌全域 · 肝转移为重点亚方向</section>')
    # 速览
    s.append(card(f'<span style="color:{B};font-weight:bold;font-size:17px">{d["n_papers"]}</span> 篇文献　'
                  f'<span style="color:{B};font-weight:bold;font-size:17px">{d["n_trials"]}</span> 项临床试验　'
                  f'<span style="color:{B};font-weight:bold;font-size:17px">{len(d["guidelines"])}</span> 篇指南共识　'
                  f'<span style="color:{B};font-weight:bold;font-size:17px">{len(d["preprints"])}</span> 篇预印本',
                  bg="#eef5ff", border="#bcd9f7"))
    # 导读
    s.append(sec_title("一、本期导读"))
    for h in d["hotspots"]:
        s.append(f'<p style="margin:8px 0;text-align:justify">{esc(h)}</p>')
    # Top10
    s.append(sec_title("二、Top10 精读"))
    for i, p in enumerate(d["top10"], 1):
        r = p.get("reading", {})
        inner = (f'<p style="margin:0 0 6px"><b style="font-size:15.5px">{i}. {esc(p.get("cn_title") or p["title"])}</b></p>'
                 f'<p style="margin:0 0 6px;color:{M};font-size:13px">{esc(p.get("journal",""))} · {esc(p.get("date",""))} · '
                 f'IF {p.get("if_value") or "—"} · <b style="color:{B}">{p.get("grade","")} 级 {p.get("score",0)} 分</b> · '
                 f'<a href="{p.get("url","")}" style="color:{B}">原文链接</a></p>')
        if r.get("background"):
            inner += f'<p style="margin:4px 0"><b>背景：</b>{esc(r["background"])}</p>'
        if r.get("results"):
            inner += f'<p style="margin:4px 0"><b>结果：</b>{esc(r["results"])}</p>'
        if r.get("significance"):
            inner += f'<p style="margin:4px 0"><b>意义：</b>{esc(r["significance"])}</p>'
        s.append(card(inner))
    # 指南
    if d["guidelines"]:
        s.append(sec_title("三、指南与共识"))
        for g in d["guidelines"]:
            s.append(card(f'<p style="margin:0"><b>{esc(g.get("cn_title") or g["title"])}</b><br>'
                          f'<span style="color:{M};font-size:13px">{esc(g.get("journal",""))} · {esc(g.get("date",""))} · '
                          f'{"中国" if g.get("china") else "国际"} · <a href="{g.get("url","")}" style="color:{B}">原文</a></span></p>'
                          + (f'<p style="margin:6px 0 0">{esc(g.get("insight",""))}</p>' if g.get("insight") else "")))
    # 试验
    s.append(sec_title(f"四、临床试验动态（{len(d['trials'])} 项，精选 10 项）"))
    ts = sorted(d["trials"], key=lambda t: t.get("enrollment") or 0, reverse=True)[:10]
    for t in ts:
        s.append(f'<p style="margin:6px 0"><b style="color:{B}">{esc(t.get("regid",""))}</b>　{esc(t.get("cn_title") or t.get("title",""))}'
                 f'<span style="color:{M};font-size:13px">　{esc(t.get("phase",""))} ｜ {esc(t.get("status",""))}'
                 + (" ｜ <b style=\"color:#c2410c\">肝转移</b>" if "liver metastas" in (t.get("title") or "").lower() else "") + '</span></p>')
    # 文献清单
    s.append(sec_title("五、本期文献精选（评分降序前 30）"))
    for topic in ("肝转移", "肠癌总体"):
        tp = [p for p in d["jp"] if p.get("topic") == topic][:15]
        if not tp:
            continue
        s.append(f'<p style="margin:12px 0 6px"><b style="color:#c2410c">{topic}（节选）</b></p>')
        for p in tp:
            cn = p.get("cn_title") or p["title"]
            s.append(f'<p style="margin:5px 0;font-size:14px">· <b>{esc(cn)}</b>'
                     f'<span style="color:{M};font-size:13px">　{esc(p.get("journal",""))} · {p.get("grade","")} 级 · {p.get("score",0)} 分 · '
                     f'<a href="{p.get("url","")}" style="color:{B}">原文</a></span></p>')
    # 尾部
    s.append(f'<section style="margin-top:26px;padding:14px 16px;background:#eef5ff;border-radius:10px;text-align:center">'
             f'<p style="margin:0;font-size:14px">本期共收录文献 {d["n_papers"]} 篇，完整清单（可按等级/主题/分类筛选）见线上周报</p>'
             f'<p style="margin:6px 0 0"><a href="{SITE_URL}" style="color:{B};font-weight:bold">点击阅读完整周报</a>　'
             f'<span style="color:{M};font-size:13px">每周一 09:00 自动更新</span></p></section>')
    s.append('</section>')
    return "\n".join(s)


def gzh_markdown(d):
    """公众号文章 Markdown 版"""
    L = [f"# 结直肠癌研究周报", ""]
    L.append(f"> **{d['ws']} 至 {d['we']}** ｜ 肠癌全域 · 肝转移为重点亚方向 ｜ 每周一自动更新")
    L.append("")
    L.append(f"**本期速览：** 收录文献 **{d['n_papers']}** 篇 ｜ 临床试验 **{d['n_trials']}** 项 ｜ "
             f"指南共识 **{len(d['guidelines'])}** 篇 ｜ 预印本 **{len(d['preprints'])}** 篇")
    L.append("")
    L.append("## 一、本期导读")
    L.append("")
    for h in d["hotspots"]:
        L.append(f"- {h}")
    L.append("")
    L.append("## 二、Top10 精读")
    L.append("")
    for i, p in enumerate(d["top10"], 1):
        r = p.get("reading", {})
        L.append(f"### {i}. {p.get('cn_title') or p['title']}")
        L.append("")
        L.append(f"*{p.get('journal','')} · {p.get('date','')} · IF {p.get('if_value') or '—'} · "
                 f"**{p.get('grade','')} 级 {p.get('score',0)} 分** · [原文]({p.get('url','')})*")
        L.append("")
        if r.get("background"):
            L.append(f"**背景：** {r['background']}")
        if r.get("results"):
            L.append("")
            L.append(f"**结果：** {r['results']}")
        if r.get("significance"):
            L.append("")
            L.append(f"**意义：** {r['significance']}")
        L.append("")
    if d["guidelines"]:
        L.append("## 三、指南与共识")
        L.append("")
        for g in d["guidelines"]:
            L.append(f"### {g.get('cn_title') or g['title']}")
            L.append("")
            L.append(f"*{g.get('journal','')} · {g.get('date','')} · {'中国' if g.get('china') else '国际'} · "
                     f"[原文]({g.get('url','')})*")
            if g.get("insight"):
                L.append("")
                L.append(g["insight"])
            L.append("")
    L.append(f"## 四、临床试验动态（{len(d['trials'])} 项，精选 10 项）")
    L.append("")
    ts = sorted(d["trials"], key=lambda t: t.get("enrollment") or 0, reverse=True)[:10]
    for t in ts:
        lm = " 🟠 **肝转移**" if "liver metastas" in (t.get("title") or "").lower() else ""
        L.append(f"- **{t.get('regid','')}** {t.get('cn_title') or t.get('title','')} ｜ {t.get('phase','')} ｜ {t.get('status','')}{lm}")
    L.append("")
    L.append("## 五、本期文献精选（评分降序前 30）")
    L.append("")
    for topic in ("肝转移", "肠癌总体"):
        tp = [p for p in d["jp"] if p.get("topic") == topic][:15]
        if not tp:
            continue
        L.append(f"### {topic}（节选）")
        L.append("")
        for p in tp:
            cn = p.get("cn_title") or p["title"]
            L.append(f"- **{cn}** ｜ {p.get('journal','')} · {p.get('grade','')} 级 · {p.get('score',0)} 分 · [原文]({p.get('url','')})")
            if p.get("insight"):
                L.append(f"  - {p['insight']}")
        L.append("")
    L.append("---")
    L.append("")
    L.append(f"本期共收录文献 {d['n_papers']} 篇，完整清单（可按等级/主题/分类筛选）见[线上周报]({SITE_URL})。")
    L.append("")
    return "\n".join(L)


def main():
    d = load_latest()
    os.makedirs(OUT, exist_ok=True)
    short, std = moments_text(d)
    with open(os.path.join(OUT, f"朋友圈文案_{d['we']}.txt"), "w", encoding="utf-8") as f:
        f.write(f"—— 简短版（配图发圈） ——\n\n{short}\n\n\n—— 标准版（纯文字发圈） ——\n\n{std}\n")
    share_card(d)
    html = gzh_html(d)
    with open(os.path.join(OUT, f"公众号文章_{d['we']}.html"), "w", encoding="utf-8") as f:
        f.write('<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                '<title>结直肠癌研究周报</title>\n</head>\n<body style="background:#fff;margin:0;padding:20px 8px">\n'
                + html + '\n</body>\n</html>\n')
    with open(os.path.join(OUT, f"公众号文章_{d['we']}.md"), "w", encoding="utf-8") as f:
        f.write(gzh_markdown(d))
    print(f"生成完成：朋友圈文案 2 版 + 配图卡片 + 公众号 HTML（{len(html)} 字符）")

if __name__ == "__main__":
    main()
