#!/bin/bash
# AlohaMini2 单右臂 Host 启动脚本（**树莓派侧**）。
#
# 与服务器侧 run_pi05_eval.sh 配对，参数必须完全对上：
#   --robot_model alohamini2 --parts right_arm --cameras forward,wrist_right
#
# 用法（在树莓派上）：
#   bash tests/yuntao/run_host_right_arm.sh           # 前台启动（Ctrl-C 停止）
#   bash tests/yuntao/run_host_right_arm.sh --daemon  # 后台 + 写日志
#   bash tests/yuntao/run_host_right_arm.sh --stop    # 停止
#
# 两个必须知道的坑：
#
#  (1) calibrate() 里有一个**交互式** input() 提示：
#        "Press ENTER to use provided calibration file associated with the id ..."
#      非交互启动必须给它喂一个换行，否则 EOFError / 卡住。
#      所以这里用 < /tmp/am_host_stdin 提供 stdin（内容就是一个换行）。
#      按 'c' 会进入重新校准流程 —— 我们没有 TTY，绝不这么做。
#
#  (2) --parts right_arm 是关键：只连接右臂总线，
#      升降轴不归零、不读高度，底盘不上电。全机模式（默认 all）会去归零升降轴。
set -uo pipefail

REPO="$HOME/MyProject/lerobot_alohamini"
LOG=/tmp/am_host.log
PIDFILE=/tmp/am_host.pid
STDIN=/tmp/am_host_stdin

ROBOT_MODEL=alohamini2
PARTS=right_arm
CAMERAS=forward,wrist_right

if [ "${1:-}" = "--stop" ]; then
  if [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "已发送停止信号给 pid $(cat "$PIDFILE")"
  elif pkill -f "alohamini_host --robot_model $ROBOT_MODEL"; then
    echo "已按进程名停止 host"
  else
    echo "没找到在跑的 host"
  fi
  exit 0
fi

if [ "${1:-}" = "--daemon" ]; then
  rm -f "$PIDFILE"
  setsid nohup "$0" > "$LOG" 2>&1 < /dev/null &
  for _ in $(seq 1 30); do
    [ -s "$PIDFILE" ] && break
    sleep 0.5
  done
  echo "=== log: $LOG"
  echo "=== pid: $(cat "$PIDFILE" 2>/dev/null || echo '(未写入，检查日志)')"
  echo "=== 停止：bash $0 --stop"
  exit 0
fi

source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate lerobot_alohamini
cd "$REPO" || exit 1

# 不设这个的话，stdout 重定向到文件会块缓冲，tail -f 日志要积够几 KB 才出东西。
export PYTHONUNBUFFERED=1

printf '\n' > "$STDIN"
echo "=== host: $ROBOT_MODEL / parts=$PARTS / cameras=$CAMERAS ==="
echo "=== log: $LOG   停止：bash $0 --stop ==="

echo $$ > "$PIDFILE"
exec python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model "$ROBOT_MODEL" \
  --parts "$PARTS" \
  --cameras "$CAMERAS" \
  < "$STDIN"
