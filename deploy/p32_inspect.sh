#!/bin/bash
# P32 一镜一图一段架构 2 周巡检报告
# 部署日: 2026-05-01 (commit ec360c9)
# 跑法: bash /opt/ssp/deploy/p32_inspect.sh
# crontab: 0 4 15 5 * /usr/bin/bash /opt/ssp/deploy/p32_inspect.sh >> /var/log/p32_inspect.log 2>&1
#         (UTC 4:00 May 15 = 本地 12:00,自动跑 1 次)

set -u

LOG=/opt/ssp/backend/app/logs/ai_platform.log
REPORT="/var/log/p32_inspect_$(date +%Y%m%d_%H%M%S).txt"

{
  echo "===================================="
  echo "  P32 一镜一图巡检 $(date '+%Y-%m-%d %H:%M:%S')"
  echo "  Window: 2026-05-01 部署 → $(date '+%Y-%m-%d') 巡检"
  echo "===================================="
  echo

  # 1. 总提交数
  N_SUBMIT=$(grep "ad_video/generate submitted" $LOG 2>/dev/null | wc -l)
  echo "[1] ad_video/generate 提交总次数: $N_SUBMIT"

  # 2. compose_first_frame_for_scene 直接失败 (函数返 error)
  N_FAIL=$(grep -E "compose_first_frame_for_scene 失败" $LOG 2>/dev/null | wc -l)
  echo "[2] compose_first_frame_for_scene 函数失败: $N_FAIL"

  # 3. 回退共享首帧的 log_warning (业务失败 + 异常 fallback 总和)
  N_FALL=$(grep -E "首帧合成失败,回退共享首帧|首帧合成异常,回退共享首帧" $LOG 2>/dev/null | wc -l)
  echo "[3] 回退共享首帧总命中: $N_FALL"

  # 4. AI 带货视频生成超时 (Seedance 15min cap)
  N_TIMEOUT=$(grep -E "AI 带货视频生成超时" $LOG 2>/dev/null | wc -l)
  echo "[4] AI 带货视频生成超时(15 分钟): $N_TIMEOUT"

  echo
  echo "[判断]"
  if [ "$N_SUBMIT" -le 0 ]; then
    echo "  ⚠ 2 周窗口内零提交,P32 无法验证。可能是用户没真用 ad-video。"
  elif [ "$N_FALL" -le 0 ]; then
    echo "  ✅ 0 fallback,P32 一镜一图首帧合成完美稳定。"
  else
    # 估算 fallback 率:每个 job 平均 ~6 段(60s),N_SUBMIT × 6 是段数上限估算
    EST_SEGS=$((N_SUBMIT * 6))
    if [ "$EST_SEGS" -gt 0 ]; then
      RATE=$((N_FALL * 100 / EST_SEGS))
      echo "  fallback 命中: $N_FALL / 估算总段数 $EST_SEGS (≈ ${RATE}%)"
      if [ "$RATE" -gt 10 ]; then
        echo "  ⚠ fallback 率 > 10%,建议给 compose_first_frame_for_scene 加 retry"
      else
        echo "  ✅ fallback 率 ≤ 10%,P32 稳定"
      fi
    fi
  fi

  echo
  echo "[最近 10 条 fallback 警告(若有)]"
  grep -E "首帧合成失败,回退共享首帧|首帧合成异常,回退共享首帧" $LOG 2>/dev/null | tail -10

  echo
  echo "===================================="
  echo "  报告写入: $REPORT"
} | tee -a "$REPORT"
