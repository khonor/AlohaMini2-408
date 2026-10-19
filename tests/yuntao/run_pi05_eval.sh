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
# (4) ⚠️ 网络拓扑（实测复核）：服务器到树莓派 10.42.0.186 **没有路由**
#      （树莓派挂在操作端 PC 的 WiFi 热点 10.42.0.0/24 上，而服务器在 10.23.x.0/22）。
#      服务器 ping 10.42.0.186 100% 丢包。
#      所以必须在操作端 PC 上开反向隧道，让服务器用 127.0.0.1 访问：
#
#        # 在操作端 PC 上执行（推荐用封装好的脚本，它会先做各项预检）
#        bash tests/yuntao/run_tunnel.sh
#
#      等价于手敲：
#        ssh -N -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \
#            -R 5555:10.42.0.186:5555 -R 5556:10.42.0.186:5556 \
#            pi05-server
#
#      ⚠️ 服务器地址用 ~/.ssh/config 别名 pi05-server，不硬编码 IP
#         （这台服务器 IP 换过：10.23.12.237 → 10.23.14.66）。
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
# 数据集名字。**不需要手动删旧目录**：
#   - 不指定 EVAL_NAME（用默认值）且目录已存在 → 自动加时间戳后缀，接着跑
#   - 显式指定了 EVAL_NAME 且已存在        → 报错（避免悄悄覆盖你指定的名字）
#   - OVERWRITE=1                          → 删掉已存在的同名目录再跑
EVAL_NAME_EXPLICIT="${EVAL_NAME:-}"
EVAL_NAME="${EVAL_NAME:-eval_pi05_am2_right_arm}"
OVERWRITE="${OVERWRITE:-0}"

# 每集开始前把右臂送回「示教起始姿态」。
# 为什么必须做：训练数据的每一集都是从同一个起始姿态开始示教的，
# 而 π0.5 是模仿学习 —— 从别的姿态起步，state 会被离散化饱和（提示词出现 0/255），
# 模型输出就跑飞，之后越偏越远。实测：起始姿态越离谱，那一集基本必废。
# 1 = 开启（推荐）；0 = 关闭，自己保证起始姿态。
RESET_HOME="${RESET_HOME:-1}"
HOME_POSE="${HOME_POSE:--2.451,-0.355,2.863,65.328,-8.801,-0.568,5.456}"
# 回位时的最大关节速度（Feetech 归一化单位/秒），越小越慢越安全
HOME_SPEED="${HOME_SPEED:-30}"

# 集与集之间等多久（秒）。这段时间用来把物块摆回原位。
# ⚠️ 倒计时结束会**自动**开始下一集，不需要你按任何键。
RESET_TIME="${RESET_TIME:-10}"

# 跑完是否直接上传到 Hugging Face Hub。
# 需要：网络能通（走 PC 代理）+ `hf auth login` 过的 token。
PUSH_TO_HUB="${PUSH_TO_HUB:-false}"

# 1 = 跳过两个**慢**的自检（处理器自检 ~30-60s、只读探针 ~2s），直接上实机。
# 内存预检和隧道检测这两个**瞬时**检查始终保留（它们防止你把共享服务器搞崩）。
SKIP_CHECKS="${SKIP_CHECKS:-0}"

PROJ=/data/home/yuntao/MyProject/2_Alohamini2
REPO="$PROJ/lerobot_alohamini"
PY="${PY:-/data/home/yuntao/miniconda3/envs/lerobot_alohamini/bin/python}"

# checkpoint 默认优先用 NVMe 上的拷贝。
# 原因：权重 8.8 GB，/dev/sdb5 是机械盘且长期被别人抢（实测顺序读 6.6 MB/s，
# mmap 加载只有 ~1 MB/s → 一次要两小时以上）；/tmp 挂的是 NVMe，约 2 分钟。
# 拷贝命令见同目录的 pi05_实机推理_Runbook.md §2。
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
else
  echo "checkpoint: $CKPT  （NVMe，加载约 1～2 分钟）"
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

# ------------------------------------------------------------------ 0a) 内存预检（很重要）
# 【实测数据，2026-10-19 用 loadtest 量出来的】
#   PI05Policy.from_pretrained() 的**进程峰值 RSS = 18.3 GB**，耗时 ~106s。
#   （之前按理论估的 14.5 GB 是低估的：构造 fp32 模型只是其中一部分，
#     加载过程中还有中间副本，实测 VmHWM 到 18347 MB。）
#   加载完成后会回落到稳定值，但**峰值决定你能不能被杀**。
#
# 这台机器只有 31 GB 内存且多人共用。实测观察到别的作业内存在 15～24 GB
# 之间大幅摆动 —— 只要有另一份作业同时涨，就会把我们的进程顶掉。
# 症状就是 bash 报「已杀死」(SIGKILL, exit 137)，且加载完成后进程就没了。
#
# 实测边界（同一天两次）：
#   available 18.7 GB → 加载成功
#   available 18.3 GB → 被 SIGKILL
# 所以门槛取 19 GB（比实测成功点略高一点），够用又不至于过度拦人。
# 确实知道自己在干什么可以用 NEED_MB=0 跳过。
NEED_MB="${NEED_MB:-19000}"
AVAIL_MB=$(free -m | awk 'NR==2{print $7}')
echo "===== [0/4] 内存预检（π0.5 加载实测峰值 18.3 GB）"
if [ "${NEED_MB}" -gt 0 ] && [ "${AVAIL_MB:-0}" -lt "${NEED_MB}" ]; then
  echo "!! 可用内存只有 ${AVAIL_MB} MB，低于需要的 ${NEED_MB} MB —— 拒绝启动，"
  echo "   否则加载 π0.5 时会 OOM 被杀（SIGKILL），甚至把整台共享服务器拖垮。"
  echo "   （实测峰值 18.3 GB；之前就在 available 18.3 GB 时被杀过一次。）"
  echo
  echo "   当前占用内存最多的进程："
  ps -eo rss,user,comm --sort=-rss | head -5 | awk 'NR==1{print "     "$0} NR>1{printf "     %6.1f GB  %-10s %s\n", $1/1048576, $2, $3}'
  echo
  echo "   可选做法："
  echo "     1) 等上面这些用户的作业跑完再试（首选）"
  echo "     2) free -m 手动确认 available 够了再来"
  echo "     3) 想赌一把（有被杀的风险）：NEED_MB=18000 bash tests/yuntao/run_pi05_eval.sh"
  exit 1
fi
echo "  [OK] available ${AVAIL_MB} MB ≥ ${NEED_MB} MB"

# ------------------------------------------------------------------ 0b) 数据集目录（自动避让，无需手动删）
# LeRobotDataset.create() 里是 `root.mkdir(parents=True, exist_ok=False)`，
# 目录已存在会抛 FileExistsError。与其让你每次手动删，这里自动处理：
#   OVERWRITE=1                  → 删掉同名目录
#   用默认 EVAL_NAME 且已存在     → 自动加时间戳后缀，保留旧数据
#   显式指定 EVAL_NAME 且已存在   → 报错（不悄悄覆盖你指定的名字）
DATASET_DIR="${HF_LEROBOT_HOME:-$HOME/.cache/huggingface/lerobot}/$HF_USER/$EVAL_NAME"
echo "===== [0b/4] 数据集目录"
if [ -e "$DATASET_DIR" ]; then
  if [ "$OVERWRITE" = "1" ]; then
    # 两道护栏，防止误删：
    #  1) 必须是「长得像 LeRobot 数据集」的目录（有 meta/info.json）
    #  2) **不 rm**，只移动到回收站目录，随时能找回
    if [ ! -f "$DATASET_DIR/meta/info.json" ]; then
      echo "!! OVERWRITE=1 但 $DATASET_DIR 不像 LeRobot 数据集（缺 meta/info.json）"
      echo "   为了安全不动它。请自己确认后手动处理，或换 EVAL_NAME。"
      exit 1
    fi
    TRASH_DIR="${HF_LEROBOT_HOME:-$HOME/.cache/huggingface/lerobot}/.trash"
    mkdir -p "$TRASH_DIR"
    TRASH_DEST="$TRASH_DIR/$(basename "$DATASET_DIR").$(date +%Y%m%d_%H%M%S)"
    mv -- "$DATASET_DIR" "$TRASH_DEST"
    echo "  OVERWRITE=1 → 旧数据集已移到回收站（不是删除）"
    echo "     $TRASH_DEST"
    echo "     确认不要了再：rm -rf \"$TRASH_DEST\""
    echo "     想恢复：mv \"$TRASH_DEST\" \"$DATASET_DIR\""
  elif [ -z "$EVAL_NAME_EXPLICIT" ]; then
    SUFFIX="$(date +%m%d_%H%M%S)"
    OLD_NAME="$EVAL_NAME"
    EVAL_NAME="${EVAL_NAME}_${SUFFIX}"
    DATASET_DIR="${HF_LEROBOT_HOME:-$HOME/.cache/huggingface/lerobot}/$HF_USER/$EVAL_NAME"
    echo "  默认名字已被占用 → 自动改名（旧的保留，不用删）"
    echo "    旧：$OLD_NAME"
    echo "    新：$EVAL_NAME"
  else
    echo "!! 你显式指定的 EVAL_NAME 已存在：$DATASET_DIR"
    echo "   （显式指定的名字不会被自动改，避免你搞不清数据写到哪了）"
    echo
    echo "   三种解决办法："
    echo "     1) 让它自动改名：去掉 EVAL_NAME，直接重用默认名"
    echo "        bash tests/yuntao/run_pi05_eval.sh"
    echo "     2) 覆盖：OVERWRITE=1 EVAL_NAME=$EVAL_NAME bash tests/yuntao/run_pi05_eval.sh"
    echo "     3) 换名字：EVAL_NAME=${EVAL_NAME}_$(date +%m%d_%H%M) bash tests/yuntao/run_pi05_eval.sh"
    exit 1
  fi
fi
# 这里再断言一次：后面 evaluate_bi.py 会 mkdir，必须真的不存在
if [ -e "$DATASET_DIR" ]; then
  echo "!! 仍然存在，无法创建：$DATASET_DIR"; exit 1
fi
echo "  [OK] 本次数据集：$HF_USER/$EVAL_NAME"
echo "       $DATASET_DIR"

# ------------------------------------------------------------------ 0) 隧道 / 链路预检
# 服务器到树莓派没有路由，必须靠操作端 PC 的反向隧道。先确认服务器的
# 127.0.0.1:5555/5556 上有监听，否则后面 connect() 会白等 5 秒然后失败。
if [ "$PI_IP" = "127.0.0.1" ]; then
  echo "===== [1/4] 检查反向隧道（服务器 loopback 的 5555/5556）"
  if ! ss -ltn 2>/dev/null | grep -qE '127\.0\.0\.1:555[56]'; then
    echo "!! 服务器上没有 127.0.0.1:5555/5556 监听 —— 隧道没开。"
    echo "   请在**操作端 PC** 上执行（推荐）："
    echo "     cd /home/aaa/My_Project/7_AlohaMini2/lerobot_alohamini"
    echo "     bash tests/yuntao/run_tunnel.sh"
    echo "   或手敲："
    echo "     ssh -N -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \\"
    echo "         -R 5555:10.42.0.186:5555 -R 5556:10.42.0.186:5556 pi05-server"
    exit 1
  fi
  echo "  [OK] 隧道在监听"
fi

# ------------------------------------------------------------------ 1) 上机前自检
# 只需几十秒，先确认 checkpoint 的 rename / 相对动作配对 / 分词都还在。
if [ "$SKIP_CHECKS" = "1" ]; then
  echo
  echo "===== [2/4][3/4] 已跳过（SKIP_CHECKS=1）"
else
  echo
  echo "===== [2/4] 推理前自检（不接硬件）"
  "$PY" tests/yuntao/verify_pi05_inference.py --ckpt "$CKPT"

  # ---------------------------------------------------------------- 2) 只读链路探针
  # 只 connect() + get_observation()，**绝不 send_action()**，机械臂不会动。
  # 顺便打印当前关节角，用来核对是否落在训练分布内。
  echo
  echo "===== [3/4] 只读链路探针（不动机械臂）"
  "$PY" tests/yuntao/probe_alohamini_link.py \
    --remote-ip "$PI_IP" --robot-model "$ROBOT_MODEL" \
    --parts "$PARTS" --cameras "$CAMERAS" || true
fi

# ------------------------------------------------------------------ 3) 实机评估
echo
echo "===== [4/4] 实机推理：$N_EPISODES 个 episode × ${EPISODE_TIME}s @ ${EVAL_FPS}Hz"
echo "📌 每跑完一集会自动保存，然后等 ${RESET_TIME}s 把物块摆回原位，**自动**开始下一集"
echo "   （不需要按任何键）。想提前停就 Ctrl-C，已经跑完的集会完整保留。"
echo
echo "主机端（在树莓派上，需先手动启动）："
echo "  bash ~/MyProject/lerobot_alohamini/tests/yuntao/run_host_right_arm.sh"
echo
echo "⚠️ 启动前把右臂摆到示教起始姿态附近（否则 state 离散化会饱和）："
echo '   --state="-2.451, -0.355, 2.863, 65.328, -8.801, -0.568, 5.456"'
echo

RESET_HOME_ARG=()
if [ "$RESET_HOME" = "1" ]; then
  # ⚠️ 必须用 `--opt=value` 的等号写法，不能写成 `--opt value`。
  # 因为 HOME_POSE 以负号开头（-2.451,...），argparse 会把 `-2.451,...`
  # 当成一个新的选项，报 "argument --eval.reset_home: expected one argument"。
  RESET_HOME_ARG=(
    "--eval.reset_home=$HOME_POSE"
    "--eval.reset_home_max_speed=$HOME_SPEED"
  )
fi

"$PY" examples/alohamini/evaluate_bi.py \
  --eval.n_episodes "$N_EPISODES" \
  --fps "$EVAL_FPS" \
  --eval.episode_time_s "$EPISODE_TIME" \
  --eval.reset_time_s "$RESET_TIME" \
  "${RESET_HOME_ARG[@]}" \
  --dataset.single_task "$TASK" \
  --policy.path "$CKPT" \
  --dataset.repo_id "$HF_USER/$EVAL_NAME" \
  --dataset.push_to_hub="$PUSH_TO_HUB" \
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
echo "  ${HF_LEROBOT_HOME:-$HOME/.cache/huggingface/lerobot}/$HF_USER/$EVAL_NAME"
echo "  （下次直接重跑即可：默认名字被占用会自动加时间戳，不需要手动删）"
echo
echo "上传到 Hugging Face："
echo "  PUSH_TO_HUB=true bash tests/yuntao/run_pi05_eval.sh     # 跑完自动传"
echo "  # 或者单独上传已经跑好的："
echo "  bash tests/yuntao/push_eval_dataset.sh $HF_USER/$EVAL_NAME"
