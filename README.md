# 肠癌肝转移文献周报（CRLM Weekly）

围绕**结直肠癌肝转移**的文献周报速递网站：每周自动汇总 PubMed 期刊论文、bioRxiv/medRxiv 预印本、ClinicalTrials.gov 与 ChiCTR 临床试验，生成中文摘要、主题分类、影响因子与中科院分区徽章，渲染为单文件静态网站。

## 快速开始

```bash
# 每周更新（默认抓取过去 7 天）
python update.py

# 指定时间窗（如首次补齐近 4 周）
python update.py --days 28
```

运行后打开 `site/index.html` 即可浏览（双击即可，或部署到任意静态服务器）。

## 网站功能

- 按周分组的文献卡片列表（最新在前）
- 关键词即时搜索（标题/摘要/期刊）
- 来源筛选：期刊论文 / 预印本 / 临床试验
- 主题分类：外科手术、系统治疗、局部治疗、转化研究、生物标志物、预后模型、综合及其他、临床试验
- 每条文献：原文链接、期刊、日期、IF（JCR 2024）、中科院分区（2025）、被引数、中文一句话摘要

## 中文摘要配置（可选，推荐）

脚本支持 `.env` 配置文件（推荐）或环境变量两种方式，任选其一：

**方式一：.env 文件（推荐，一次配置永久生效）**

```bash
cp .env.example .env
# 编辑 .env，填入您的 API Key
```

**方式二：环境变量**

```bash
export LLM_API_KEY="sk-..."                          # 必填
export LLM_BASE_URL="https://api.deepseek.com"       # 可选，默认 DeepSeek
export LLM_MODEL="deepseek-chat"                     # 可选
```

**获取 API Key**（任选一家，均为 OpenAI 兼容协议）：

| 平台 | 注册地址 | base_url |
|---|---|---|
| DeepSeek（推荐，性价比高） | https://platform.deepseek.com | `https://api.deepseek.com` |
| 阿里通义千问 | https://bailian.console.aliyun.com | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Moonshot Kimi | https://platform.moonshot.cn | `https://api.moonshot.cn/v1` |
| OpenAI | https://platform.openai.com | `https://api.openai.com/v1` |

配置后运行 `python update.py`，日志显示「LLM 模式（模型名）」即生效。注意：`.env` 含密钥，请勿提交到公开仓库。

## 目录结构

```
crlm_weekly/
├── update.py            # 一键更新脚本
├── config.yaml          # 检索式、时间窗、分类等配置
├── template/            # 网站模板
├── data/
│   ├── papers.jsonl     # 累计文献数据库（自动去重）
│   ├── trials.jsonl     # 累计临床试验数据库
│   └── if_map.json      # 期刊 IF / 中科院分区对照表
└── site/
    └── index.html       # 生成的网站（单文件，可直接部署）
```

## 数据源与说明

| 来源 | 内容 | 说明 |
|---|---|---|
| PubMed | 期刊论文 | 严格 CRLM 检索式，可在 config.yaml 调整 |
| bioRxiv / medRxiv | 预印本 | 按日期窗口拉取后关键词过滤 |
| ClinicalTrials.gov | 临床试验 | 按最近更新日期过滤 |
| ChiCTR | 中国临床试验 | 无公开稳定 API，best-effort 抓取，失败自动跳过 |

- **影响因子**：JCR 2024 版 + 中科院分区 2025 版（来源：ShowJCR 合并数据集），为静态对照表，每年可手动更新 `data/if_map.json`；预印本无 IF。
- **被引数**：来自 Europe PMC，新发表文献通常为 0，属正常现象。
- **摘要免责**：中文摘要由 AI 自动生成，仅供参考，请以原文为准。

## 部署

`site/index.html` 为完全自包含的单文件（内联 CSS/JS），上传到任意静态托管（Nginx、GitHub Pages、对象存储等）即可访问；配合 cron 每周运行 `update.py` 并重新上传即可保持更新。

## 定时更新

**方式一：GitHub Actions（推荐，免服务器）**

1. 将本项目推送到 GitHub 仓库（私有仓库即可）
2. 仓库 Settings → Secrets and variables → Actions 添加 `LLM_API_KEY`（可选 `LLM_BASE_URL`、`LLM_MODEL`）
3. 工作流 `.github/workflows/weekly.yml` 会在每周一 09:00（北京时间）自动运行：抓取 → LLM 精读 → 渲染 → 提交发布；也可在 Actions 页面手动触发（workflow_dispatch）
4. 配合 GitHub Pages：Settings → Pages → Source 选 main 分支 `/site` 目录，每次更新后网页自动刷新

**方式二：自有服务器 cron**

```bash
# 每周一 09:00 运行（crontab -e 添加）
0 9 * * 1 bash /path/to/crlm_weekly/run_weekly.sh >> /path/to/crlm_weekly/data/cron.log 2>&1
```

`run_weekly.sh` 会执行更新并在 git 仓库环境下自动提交推送；运行日志写入 `data/update_*.log`。

**注意**：LLM 精读消耗 API 余额（DeepSeek 每周约 0.3 元），请确保账户余额充足；预印本 API 偶发超时属预期，脚本会告警后继续。
