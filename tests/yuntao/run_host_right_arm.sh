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
  # ⚠️ 必须用 SIGINT（kill -INT），不能用 SIGTERM（kill 默认）。
  # 原因：alohamini_host 的 main() 只捕获 KeyboardInterrupt：
  #     try: ... except KeyboardInterrupt: ... finally: robot.disconnect()
  # 而 disconnect() 里才是 `bus.disconnect(disable_torque_on_disconnect=True)`，
  # 也就是**真正松力矩的那一步**。
  # 用 SIGTERM 的话进程直接死，finally 不执行，串口一关舵机就保持最后的力矩 ——
  # 表现就是「机械臂被锁住、掰不动」。
  PID=""
  [ -f "$PIDFILE" ] && PID="$(cat "$PIDFILE")"
  if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    PID=$(pgrep -f "alohamini_host --robot_model $ROBOT_MODEL" | head -1)
  fi
  if [ -z "$PID" ]; then
    echo "没找到在跑的 host"
    exit 0
  fi
  echo "向 host (pid $PID) 发送 SIGINT，让它走 finally 松力矩…"
  kill -INT "$PID" 2>/dev/null
  for _ in $(seq 1 30); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "  !! 10 秒内没退出，改用 SIGTERM（注意：可能不会松力矩）"
    kill -TERM "$PID" 2>/dev/null
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
  else
    echo "  [OK] host 已退出（力矩应已释放，机械臂可以手动掰了）"
  fi
  rm -f "$PIDFILE"
  exit 0
fi

if [ "${1:-}" = "--daemon" ]; then
  # 已经在跑就不要重复启动。
  # 否则第二个进程会在 `cam.connect()` 处失败，报的是
  #   ConnectionError: Failed to open OpenCVCamera(/dev/am_camera_forward)
  # —— 看起来像"相机坏了"，其实是相机已经被前一个 host 打开了。
  # 这个误导性报错很难查，所以这里先拦一道。
  EXISTING=$(pgrep -f "alohamini_host --robot_model $ROBOT_MODEL" | head -1)
  if [ -n "${EXISTING:-}" ]; then
    echo "$EXISTING" > "$PIDFILE"
    echo "!! 已经有一个 host 在跑（pid $EXISTING），不再重复启动。"
    echo "   端口和相机现在归它所有；再起一个会报「Failed to open OpenCVCamera」。"
    echo "   要重启：bash \"$0\" --stop && bash \"$0\" --daemon"
    exit 0
  fi
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
