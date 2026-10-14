#!/bin/bash
# pi05（AM2 右臂）实机推理 —— 一份可复用的启动脚本。
#
# 前置：树莓派上的 host 已经在跑（见文件末尾的 HOST_CMD）。
#
# 这个 checkpoint 有三个「不这么配就一定出问题」的点，脚本已经写好：
#
#  (1) use_relative_actions=true（gripper 除外）。相对动作策略在同步推理下，
#      每 tick 都会用**当前** state 去还原整块里弹出来的那一个动作，
#      绝对目标会随动作块逐步漂移 —— 所以必须走 RTC（异步整块后处理）。
#      lerobot-rollout 会对 sync 直接抛 NotImplementedError；
#      evaluate_bi.py 的 default 是 sync，**必须显式改成 rtc**。
#
#  (2) 相机键名。数据集里叫 forward / wrist_right，pi0.5 期望
#      base_0_rgb / left_wrist_0_rgb / right_wrist_0_rgb。训练时靠 --rename_map 映射，
#      而这份映射**已经存进 checkpoint 的 policy_preprocessor.json** 了，
#      所以 evaluate_bi.py（只覆盖 device_processor）会自动继承，不用再传。
#      ⚠️ 但 lerobot-rollout 会把这份映射**覆盖成空字典**，走 rollout 就必须显式传 --rename_map。
#
#  (3) 任务串必须与训练数据集完全一致：khonor/am2_right_arm_test 里只有一个任务
#      "pickup1"（meta/tasks.parquet）。默认值是 "robot task"，不传就是错的。
#
# 另外：只有右臂，所以 --robot.parts right_arm 必须与 host 端 --parts 一致，
# 升降轴不会归零、底盘不会上电。
#
# (4) ⚠️ 网络拓扑（实测，2026-xx 复核）：服务器 10.23.12.237 到树莓派
#      10.42.0.186 **没有路由**（树莓派挂在操作端 PC 的 WiFi 热点 10.42.0.0/24 上，
#      而服务器在 10.23.12.0/22）。服务器 ping 10.42.0.186 100% 丢包。
#      所以必须在操作端 PC 上开反向隧道，让服务器用 127.0.0.1 访问：
#
#        # 在操作端 PC 上执行，保持这个终端开着
#        ssh -N -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \
#            -R 5555:10.42.0.186:5555 -R 5556:10.42.0.186:5556 \
#            yuntao@10.23.12.237
#
#      `-R` 默认只绑服务器的 loopback，所以下面的 PI_IP 必须是 127.0.0.1。
#      端口故意原样映射 5555/5556：evaluate_bi.py 没有暴露 --robot.port_zmq_* 参数，
#      只有这样它才能不改代码直接用。
#
# (5) ⚠️ 起始姿态。pi0.5 把 observation.state 离散化（256 bin）后写进提示词，
#      超出训练 q01~q99 会饱和。实测「随便停在哪」会饱和（提示词出现 255），
#      所以上机前必须把右臂摆到示教起始姿态附近：
#          --state="-2.451, -0.355, 2.863, 65.328, -8.801, -0.568, 5.456"
#      （= 训练集 30 条 episode 首帧姿态的均值，从
#        am2_right_arm_test 的 parquet 直接算出来）
set -euo pipefail

# ------------------------------------------------------------------ 按需修改
# 走隧道时必须是 127.0.0.1；直连树莓派（操作端 PC 上直接跑）才填 10.42.0.186。
PI_IP="${PI_IP:-127.0.0.1}"              # 树莓派 IP（隧道场景 = 127.0.0.1）
HF_USER="${HF_USER:-khonor}"             # 评估数据集上传/落盘用的用户名
ROBOT_MODEL="${ROBOT_MODEL:-alohamini2}" # 必须与 host 端一致
PARTS="${PARTS:-right_arm}"
CAMERAS="${CAMERAS:-forward,wrist_right}"
TASK="${TASK:-pickup1}"                  # 必须与训练数据集的任务串一致
N_EPISODES="${N_EPISODES:-3}"
EPISODE_TIME="${EPISODE_TIME:-45}"
EVAL_FPS="${EVAL_FPS:-30}"
EVAL_NAME="${EVAL_NAME:-eval_pi05_am2_right_arm}"

PROJ=/data/home/yuntao/MyProject/2_Alohamini2
REPO="$PROJ/lerobot_alohamini"
PY="${PY:-/data/home/yuntao/miniconda3/envs/lerobot_alohamini/bin/python}"

# checkpoint 默认优先用 NVMe 上的拷贝。
# 原因：权重 8.8 GB，/dev/sdb5 是机械盘且长期被别人抢（实测顺序读 6.6 MB/s，
# mmap 加载只有 ~1 MB/s → 一次要两小时以上）；/tmp 挂的是 NVMe，约 2 分钟。
# 拷贝命令见 pi05_实机推理_Runbook.md §2。
NVME_CKPT="${NVME_CKPT:-/tmp/pi05_ckpt/pretrained_model}"
HDD_CKPT="$REPO/outputs/train/pi05_am2_right_arm/checkpoints/last/pretrained_model"
if [ -z "${CKPT:-}" ]; then
  if [ -f "$NVME_CKPT/model.safetensors" ]; then
    CKPT="$NVME_CKPT"
  else
    CKPT="$HDD_CKPT"
  fi
fi
if [ "$CKPT" = "$HDD_CKPT" ]; then
  echo "⚠️  用的是机械盘上的 checkpoint —— 加载可能要 1～2 小时。"
  echo "    建议先拷到 NVMe（见 Runbook §2），或显式传 CKPT=$NVME_CKPT"
fi

# ------------------------------------------------------------------ 环境
# 与 run_pi05_train.sh 同样的两个坑：
#   * no_proxy 不能含 "[::1]"（httpx 解析会 InvalidURL）；
#   * 分词器需要 google/paligemma-3b-pt-224，这是 gated 仓。
# 分词器已在本地缓存且补过 .no_exist 标记，所以这里直接强制离线，
# 既避免部署时被网络卡住，也顺便证明「不联网也能起」。
export no_proxy="localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
export NO_PROXY="$no_proxy"
unset http_proxy https_proxy
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1

cd "$REPO"

# ------------------------------------------------------------------ 0) 隧道 / 链路预检
# 服务器到树莓派没有路由，必须靠操作端 PC 的反向隧道。先确认服务器的
# 127.0.0.1:5555/5556 上有监听，否则后面 connect() 会白等 5 秒然后失败。
if [ "$PI_IP" = "127.0.0.1" ]; then
  echo "===== [0/3] 检查反向隧道（服务器 loopback 的 5555/5556）"
  if ! ss -ltn 2>/dev/null | grep -qE '127\.0\.0\.1:555[56]'; then
    echo "!! 服务器上没有 127.0.0.1:5555/5556 监听 —— 隧道没开。"
    echo "   请在操作端 PC 上执行："
    echo "     ssh -N -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \\"
    echo "         -R 5555:10.42.0.186:5555 -R 5556:10.42.0.186:5556 yuntao@10.23.12.237"
    exit 1
  fi
  echo "  [OK] 隧道在监听"
fi

# ------------------------------------------------------------------ 1) 上机前自检
# 只需几十秒，先确认 checkpoint 的 rename / 相对动作配对 / 分词都还在。
echo
echo "===== [1/3] 推理前自检（不接硬件）"
"$PY" tests/yuntao/verify_pi05_inference.py --ckpt "$CKPT"

# ------------------------------------------------------------------ 2) 只读链路探针
# 只 connect() + get_observation()，**绝不 send_action()**，机械臂不会动。
# 顺便打印当前关节角，用来核对是否落在训练分布内。
echo
echo "===== [2/3] 只读链路探针（不动机械臂）"
"$PY" tests/yuntao/probe_alohamini_link.py \
  --remote-ip "$PI_IP" --robot-model "$ROBOT_MODEL" \
  --parts "$PARTS" --cameras "$CAMERAS" || true

# ------------------------------------------------------------------ 3) 实机评估
echo
echo "===== [3/3] 实机推理：$N_EPISODES 个 episode × ${EPISODE_TIME}s @ ${EVAL_FPS}Hz"
echo "主机端（在树莓派上，需先手动启动）："
echo "  bash ~/MyProject/lerobot_alohamini/tests/yuntao/run_host_right_arm.sh"
echo
echo "⚠️ 启动前把右臂摆到示教起始姿态附近（否则 state 离散化会饱和）："
echo '   --state="-2.451, -0.355, 2.863, 65.328, -8.801, -0.568, 5.456"'
echo

"$PY" examples/alohamini/evaluate_bi.py \
  --eval.n_episodes "$N_EPISODES" \
  --fps "$EVAL_FPS" \
  --eval.episode_time_s "$EPISODE_TIME" \
  --eval.reset_time_s 10 \
  --dataset.single_task "$TASK" \
  --policy.path "$CKPT" \
  --dataset.repo_id "$HF_USER/$EVAL_NAME" \
  --dataset.push_to_hub=false \
  --robot.remote_ip "$PI_IP" \
  --robot.id my_alohamini \
  --robot.robot_model "$ROBOT_MODEL" \
  --robot.parts "$PARTS" \
  --robot.cameras "$CAMERAS" \
  --inference.type rtc \
  --inference.rtc.execution_horizon 10 \
  --inference.rtc.max_guidance_weight 10.0 \
  --inference.rtc.queue_threshold 30 \
  --interpolation_multiplier 1

echo
echo "评估数据集（obs + 实际下发动作 + task）落在："
echo "  \$HF_LEROBOT_HOME/$HF_USER/$EVAL_NAME   （默认 ~/.cache/huggingface/lerobot/...）"
echo "下一次评估记得换 --dataset.repo_id，该目录不能已存在。"
