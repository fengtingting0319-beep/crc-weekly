#!/usr/bin/env bash
# 每周定时更新入口：抓取 -> LLM 精读 -> 渲染 -> （可选）git 提交发布
# 用法：./run_weekly.sh [天数]   默认 7 天窗口
set -euo pipefail
cd "$(dirname "$0")"

DAYS="${1:-7}"
LOG="data/update_$(date +%Y%m%d_%H%M%S).log"

echo "[$(date '+%F %T')] 开始更新（窗口 ${DAYS} 天）" | tee -a "$LOG"
python3 update.py --days "$DAYS" 2>&1 | tee -a "$LOG"

# 可选：若在 git 仓库中且配置了远程，自动提交发布
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
    git add site/ data/
    git commit -m "weekly update $(date +%F)" || echo "无变更，跳过提交"
    git push origin HEAD || echo "推送失败，请检查远程配置"
fi

echo "[$(date '+%F %T')] 更新完成" | tee -a "$LOG"
