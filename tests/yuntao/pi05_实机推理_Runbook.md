# π0.5 实机推理 Runbook

> **本文件位置**：`lerobot_alohamini/tests/yuntao/pi05_实机推理_Runbook.md`
> 文中所有 `bash tests/yuntao/xxx.sh` 都假设你在**仓库根目录**执行：
> PC 上 `~/My_Project/7_AlohaMini2/lerobot_alohamini`，
> 服务器上 `/data/home/yuntao/MyProject/2_Alohamini2/lerobot_alohamini`。
>
> 目标：**服务器跑 π0.5 推理 → ZMQ 指令 → 树莓派 Host 控制机械臂右臂抓物块。**

---

## 0. 三条命令（跑通只需要这些）

| # | 在哪 | 命令 |
|---|---|---|
| 1 | 树莓派 | `bash tests/yuntao/run_host_right_arm.sh --daemon` |
| 2 | 操作端 PC | `bash tests/yuntao/run_tunnel.sh`（**保持开着**） |
| 3 | 服务器 | `SKIP_CHECKS=1 N_EPISODES=10 bash tests/yuntao/run_pi05_eval.sh` |

顺序不能换：Pi 是 ZMQ **服务端**（bind 5555/5556），隧道把它映射到服务器 loopback，
服务器才是客户端（connect）。跑之前先过 §1 的四条硬前置。

---

## 1. 四条硬前置

| # | 条件 | 怎么查 | 不满足会怎样 |
|---|---|---|---|
| 1 | PC 开着，WiFi 热点 `10.42.0.0/24` 通 | `ip -4 addr \| grep 10.42.0` | 树莓派连不上，隧道到不了 Pi |
| 2 | Pi 上 Host 在跑 | `ss -ltn \| grep -E '5555'`（在 Pi 上） | 服务器 `connect()` 等 5 秒后超时 |
| 3 | checkpoint 在 NVMe | `ls /tmp/pi05_ckpt/pretrained_model/model.safetensors` | 从机械盘加载 > 2 小时 |
| 4 | 服务器 available ≥ 19000 MB | `free -m \| awk 'NR==2{print $7}'` | 加载峰值 18.3 GB，不够会 **SIGKILL** |

<details>
<summary><b>前置 3 详解：NVMe 拷贝（服务器每次重启都要重做）</b></summary>

权重 8.8 GB。机械盘 `/dev/sdb5` 实测顺序读仅 6.6 MB/s、mmap 加载约 1 MB/s（多人抢盘）；
NVMe 挂在 `/tmp`，加载约 2 分钟。**`/tmp` 重启即清空**（2026-10-13 就丢过一次）。

```bash
nohup bash -c '
  SRC=/data/home/yuntao/MyProject/2_Alohamini2/lerobot_alohamini/outputs/train/pi05_am2_right_arm/checkpoints/040000/pretrained_model
  mkdir -p /tmp/pi05_ckpt && rm -rf /tmp/pi05_ckpt/pretrained_model
  cp -a "$SRC" /tmp/pi05_ckpt/ && sync && echo DONE && du -sh /tmp/pi05_ckpt/pretrained_model
' > /tmp/copy_ckpt.log 2>&1 &
```

实测 3 分 34 秒（空闲）～ 12 分 26 秒（抢盘）。`run_pi05_eval.sh` 会自动优先用它。
</details>

<details>
<summary><b>前置 4 详解：内存门槛（最容易翻车）</b></summary>

`PI05Policy.from_pretrained()` 先在 **CPU 上以 fp32 构造 3.62 B 参数**
（`modeling_pi05.py:375/383/391` 显式写死 `dtype="float32"`），再搬进 GPU。
实测**进程峰值 RSS = 18347 MB**，耗时约 106 s。

| available | 结果 |
|---|---|
| 18.7 GB | ✅ 成功 |
| 18.3 GB | ❌ 被 SIGKILL（shell 报「已杀死」，exit 137） |
| 25 GB | ✅ 成功 |

机器 31 GB 且多人共用，别人内存在 15–24 GB 之间摆动、所以门槛取 **19000 MB**。

- 被杀的判据：日志停在 `The PI05 model is a direct port of the OpenPI implementation.` 之后，
  且 exit 137。**这时数据集目录还没创建**，不会留重名冲突，直接重跑即可。
- 想赌一把：`NEED_MB=18000`。
- 彻底降峰值要改代码（meta device + `load_state_dict(assign=True)`），属改共享仓库，需组内确认。
- 显存：`evaluate_bi.py` 的 `auto_select_torch_device()` **只返回 cuda(=GPU 0)**，不会挑最空的。
  OOM 就 `CUDA_VISIBLE_DEVICES=1`。
</details>

---

## 2. 架构与网络

```text
┌──────── 操作端 PC ────────┐
│ 10.42.0.1/24 (WiFi 热点)  │  ← 树莓派连的是这个
│ 10.23.15.171/22           │
└────┬──────────────┬───────┘
     │ ssh          │ ssh -N -R 5555,5556
     ▼              ▼
 10.42.0.186    pi05-server (10.23.14.66)
 ┌─ 树莓派 ─┐    ┌──── 服务器 ────┐
 │ alohamini│◀───│ 127.0.0.1:5555 │  cmd
 │ _host    │    │ 127.0.0.1:5556 │  obs（含 JPEG）
 │ 50Hz 环  │───▶│ evaluate_bi.py │
 │ 右臂+2相机│    │ π0.5 @ 3090Ti  │
 └──────────┘    └─────────────────┘
```

**为什么必须开隧道**：服务器到 `10.42.0.186` 没有路由（实测 ping 100% 丢包，
`ip route get` 走默认网关），两网段不互通，且方向单向（PC 能 SSH 服务器，反向不通）。

**服务器 IP 会变**（`10.23.12.237 → 10.23.14.66`），所以统一用 `~/.ssh/config` 别名：

```sshconfig
Host pi05-server
  HostName 10.23.14.66
  User yuntao
```

换 IP 只改这一处，脚本全不动。临时换机器：`SERVER=yuntao@x.x.x.x bash tests/yuntao/run_tunnel.sh`。

| 端口 | socket | bind 方 | 用途 |
|---|---|---|---|
| 5555 | `zmq.PULL` | Host（树莓派） | 动作下发（CONFLATE，只留最新） |
| 5556 | `zmq.ROUTER` | Host（树莓派） | 请求/应答取观测，图像走这里 |
| 5557 | PUB | Host（可选 `--camera-stream`） | ROS 相机流，评估不用 |

---

## 3. 完整流程

### 3.1 开机前

- [ ] 急停可按到；桌面清空；人机距离 ≥ 1 m
- [ ] 场景 1:1 复刻训练时：**深色垫子 + 红色小方块在右侧 + 白色方板在左侧**
- [ ] §1 四条硬前置全绿

### 3.2 树莓派：起 Host

```bash
ssh lab408@10.42.0.186
cd ~/MyProject/lerobot_alohamini
bash tests/yuntao/run_host_right_arm.sh --daemon
tail -f /tmp/am_host.log        # 判据：无报错
ss -ltn | grep -E '5555|5556'   # 判据：两个 0.0.0.0 都在 LISTEN
```

参数写死在脚本里，**不要改**（必须与评估端逐字一致）：

| 参数 | 值 | 说明 |
|---|---|---|
| `--robot_model` | `alohamini2` | 6-DoF 从臂 profile |
| `--parts` | `right_arm` | 只连右臂总线；升降轴不归零、底盘不上电 |
| `--cameras` | `forward,wrist_right` | 与训练用的 2 路一致 |

### 3.3 操作端 PC：起隧道

```bash
cd /home/aaa/My_Project/7_AlohaMini2/lerobot_alohamini
bash tests/yuntao/run_tunnel.sh     # 保持开着
```

脚本做 5 步检查：mDNS 解析 → ping → 查 Pi 端口 → **查服务器端口是否已被占用** → 建隧道。
等价于：

```bash
ssh -N -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o ExitOnForwardFailure=yes \
    -R 5555:10.42.0.186:5555 -R 5556:10.42.0.186:5556 pi05-server
```

> Pi 的 IP 也会变。脚本优先 mDNS（`lab408.local`），失败回退 `10.42.0.186`。
> 手动指定：`PI_IP=<新IP> bash tests/yuntao/run_tunnel.sh`

### 3.4 服务器：跑评估

```bash
ssh pi05-server
cd ~/MyProject/2_Alohamini2/lerobot_alohamini
SKIP_CHECKS=1 N_EPISODES=10 EPISODE_TIME=45 bash tests/yuntao/run_pi05_eval.sh
```

脚本自证链（任何一步不过就别往下走）：

| 步骤 | 判据 |
|---|---|
| `[0/4]` 内存预检 | `available ≥ 19000`，不够会列出谁在占内存 |
| `[0b/4]` 数据集目录 | 默认名被占会自动加时间戳（见 §4.3） |
| `[1/4]` 隧道 | `127.0.0.1:5555/5556` 在监听 |
| `[2/4]` 处理器自检 | `结果：全部通过 ✅`（`SKIP_CHECKS=1` 可跳） |
| `[3/4]` 只读探针 | `链路全部正常 ✅`，**不动机械臂**（同上可跳） |
| `[4/4]` 实机推理 | 先 `[HOME]` 回位，再开跑 |

### 3.5 收工

```bash
# 树莓派：停 Host（SIGINT → 走 finally → 松力矩，机械臂变软）
bash tests/yuntao/run_host_right_arm.sh --stop
# PC：Ctrl-C 结束隧道
```

---

## 4. 跑起来之后

### 4.1 多集是**自动连续**的

流程：跑满 `episode_time_s` → `save_episode()` → **自动回位** → 倒计时 `reset_time_s`（摆物块）
→ 自动下一集，直到跑满 `n_episodes` 才退出。**中间不用按键**；想提前停就 Ctrl-C。

```
[SAVED] 第 1/10 集：1320 帧（44.0s）已写入 .../eval_pi05_am2_right_arm_1019_1015
[RESET] 第 1/10 集已保存到磁盘。接下来自动开始第 2 集 —— 不想继续就按 Ctrl-C。
[HOME] 第 2 集前 把右臂送回起始姿态：最大关节偏差 42.3，限速 30/s，约 1.4s
[HOME] 回位完成
        右臂已回到起始姿态。现在把物块摆回原位，10 秒后自动开跑。
```

> Ctrl-C 走 `finally`：存盘 + `finalize()` + 保持位置 + 断开。但 **`push_to_hub()` 在 `try/finally` 外**，
> 所以 Ctrl-C 不触发上传（用 §4.4 补传）。**别用 `kill -9` 或断电**——`finalize()` 不跑会让数据集无效。

### 4.2 每集前自动回位（关键，别关）

π0.5 把 `observation.state` 量化成 256 个 bin 写进提示词。起始姿态越偏离示教分布，
离散化越饱和（提示词出现 0/255）→ 输出跑飞 → 状态更偏，**正反馈**，一集基本就废。

**示教起始姿态（训练集 30 条 episode 首帧均值）：**

| 关节 | 起始均值 | 训练 q01 | 训练 q99 |
|---|---|---|---|
| `arm_right_shoulder_pan.pos` | **-2.451** | -42.110 | 2.462 |
| `arm_right_shoulder_lift.pos` | **-0.355** | -19.833 | 29.324 |
| `arm_right_elbow_flex.pos` | **2.863** | -23.743 | 17.714 |
| `arm_right_wrist_flex.pos` | **65.328** | 46.864 | 70.206 |
| `arm_right_wrist_yaw.pos` | **-8.801** | -19.396 | 1.054 |
| `arm_right_wrist_roll.pos` | **-0.568** | -15.562 | 5.704 |
| `arm_right_gripper.pos` | **5.456** | 3.751 | 50.922 |

摆到这一行即可（`HOME_POSE` 默认值）：

```text
-2.451,-0.355,2.863,65.328,-8.801,-0.568,5.456
```

**以前做不到**：一集跑完机械臂停在结束姿态并保持力矩，reset 那 10 秒手掰不动，
于是第 2 集从第 1 集的结束姿态起步 →「第 1 集还行、后面越来越差」。
现在 `return_home()` 在**每集前**（含第 1 集）按 `HOME_SPEED` 限速送回去。

> 实测证据：失败那次的起始姿态有 1/3/3/5 个维度越界，包括 `wrist_flex=100`（顶到机械限位）、
> `shoulder_lift=-43`、`wrist_yaw=23.6`（q99 仅 1.05）。
>
> ⚠️ **评测时不要把手伸进去掰**：会同时污染图像观测和状态。要干预就 Ctrl-C。

### 4.3 数据在哪 / 怎么不手动删

```text
~/.cache/huggingface/lerobot/<HF_USER>/<EVAL_NAME>
默认：~/.cache/huggingface/lerobot/khonor/eval_pi05_am2_right_arm
```

标准 LeRobot v3.0：`data/*.parquet`（state 7 维 + action 7 维）、`videos/`（两路 mp4）、`meta/`。

`LeRobotDataset.create()` 用 `mkdir(exist_ok=False)`，目录已存在会抛 `FileExistsError`，
**且这步在模型加载之后**。所以脚本 `[0b/4]` 自动处理，**不需要手动删**：

| 情况 | 行为 |
|---|---|
| 用默认 `EVAL_NAME` 且已存在 | **自动加时间戳**（`..._1019_1015`），旧数据保留 |
| **显式**指定 `EVAL_NAME` 且已存在 | 报错（不悄悄覆盖你指定的名字） |
| `OVERWRITE=1` | 移到 `<HF_LEROBOT_HOME>/.trash/<名字>.<时间戳>`，**永不 rm** |

> 🔒 `OVERWRITE` 两道护栏（一次误删事故后加的）：① 目标必须含 `meta/info.json`，否则拒绝；
> ② 只 `mv` 到 `.trash/`，随时可 `mv` 回来。
>
> 代价：目录会攒（每次 25–100 MB）。定期 `du -sh ~/.cache/huggingface/lerobot/khonor/*/` 清理。

### 4.4 上传 Hugging Face

```bash
# 跑完自动传
PUSH_TO_HUB=true SKIP_CHECKS=1 N_EPISODES=10 bash tests/yuntao/run_pi05_eval.sh

# 补传（Ctrl-C 中断过、或事后想传）
bash tests/yuntao/push_eval_dataset.sh                        # 默认 repo，私有
bash tests/yuntao/push_eval_dataset.sh khonor/eval_pi05_run2  # 指定 repo
PRIVATE=false bash tests/yuntao/push_eval_dataset.sh          # 传成公开
```

三个坑（脚本已处理）：① 服务器必须走 PC 代理 `http://10.23.15.171:7897`；
② 必须 `unset HF_HUB_OFFLINE`（评估脚本为防卡顿强制了离线）；③ 必须 `hf auth login` 过（当前 `khonor`）。

> **不能把两次运行追加到同一个数据集**。今天 10 集、明天 10 集 = 两个 repo；要合并得另做数据集编辑。

---

## 5. 参数速查

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `N_EPISODES` | 3 | 跑几集（自动连续） |
| `EPISODE_TIME` | 45 | 每集秒数 |
| `RESET_TIME` | 10 | 集间倒计时（摆物块） |
| `EVAL_NAME` | `eval_pi05_am2_right_arm` | 数据集名；默认名被占会自动加时间戳 |
| `OVERWRITE` | 0 | 1 = 旧数据集移到 `.trash/`（不删） |
| `RESET_HOME` | 1 | 1 = **每集前自动回位**（强烈建议保持） |
| `HOME_POSE` | 见 §4.2 | 回位目标姿态 |
| `HOME_SPEED` | 30 | 回位速度上限（归一化单位/秒） |
| `TASK` | `pickup1` | 语言指令，必须与训练一致 |
| `SKIP_CHECKS` | 0 | 1 = 跳过两个慢自检（自检 ~60s + 探针 ~2s） |
| `PUSH_TO_HUB` | false | true = 跑完自动传 HF |
| `NEED_MB` | 19000 | 内存门槛，0 = 不检查（危险） |
| `CUDA_VISIBLE_DEVICES` | 未设 | 显存不够时指定 |

> ⚠️ 直接调 `evaluate_bi.py` 且值以负号开头时**必须用等号写法**：
> `--eval.reset_home="-2.451,..."`；写成空格形式会被 argparse 当成新选项，报
> `argument --eval.reset_home: expected one argument`。用 `run_pi05_eval.sh` 不受影响。

---

## 6. 安全 SOP（别跳级）

急停在手边；人机距离 ≥ 1 m。

| 级别 | 做什么 | 通过条件 |
|---|---|---|
| **T0 台架空转** | 右臂垫高、夹爪不碰任何东西，`N_EPISODES=1 EPISODE_TIME=8` | 动作小幅平滑，不撞限位、不抖 |
| **T1 空抓** | 放物块但不指望夹住 | 夹爪朝物块方向移动并开合 |
| **T2 单次抓取** | 完整跑 1 次 | 抓起 → 放到白板上 |
| **T3 连续 10 次** | 固定摆放 | 成功率 ≥ 50% |
| **T4 随机 ±5 cm** | 物块位置随机 | 成功率 ≥ 60% |

**Host 内建安全门（源码确认）**

| 机制 | 数值 |
|---|---|
| 单写者仲裁 | 同时只有一个指令源，不抢方向盘 |
| 看门狗 | **1000 ms** 收不到指令 → 停 |
| 控制频率 | 50 Hz |
| 反馈陈旧上限 | 250 ms |
| 夹爪接触阈值 | 0.5 A |
| 持续过载 / 近堵转 | 650 ms / 80 ms 后停 |

> 这些是**保护电机**的。π0.5 让夹爪长时间顶住物体可能被判过载松开——这正是想要的行为。

---

## 7. 排障速查

| 现象 | 原因 | 先试什么 |
|---|---|---|
| `connect()` 失败 | 隧道断了 / Host 没起 | `ss -ltn \| grep 555`（服务器 + 树莓派两边查） |
| 开隧道报 `remote port forwarding failed for listen port 5555` | **服务器 5555 已被占用**（已有隧道在跑）。`ssh -R` 不能复用远端端口 | `pgrep -af 'ssh -N'` → `kill <pid>`；`run_tunnel.sh` 已会预检 |
| Host 报 `FeetechMotorsBus motor check failed ... found motor list: {}` | **舵机没供电 / 舵机线没插**（USB 板还在枚举，但电机不应答） | 查电源开关、控制板到 1 号舵机的 6pin 线、舵机间级联线 |
| Host 报 `Failed to open OpenCVCamera(/dev/am_camera_*)` | **已有另一个 host 在跑**，相机被占（USB 相机不能双开）。报错有误导性 | `pgrep -af alohamini_host`；有就 `--stop` 再起 |
| 进程「已杀死」/ exit 137 | 加载时内存不够（峰值 18.3 GB） | `free -m` 看 available，够 19 GB 再跑 |
| `enabled_parts` 不一致 | Host 与评估端 `--parts` 不同 | 两边都用 `right_arm` |
| 提示词里出现 0/255 | 起始姿态超出训练分布 | 确认 `RESET_HOME=1`，或手调 `HOME_POSE` |
| 夹爪不朝物块去 / 乱走 | 起始姿态差、手入镜、视角变了 | 看 `videos/` 回放对照训练画面 |
| 夹不住 | 闭合时机晚 / 力不足 / 物块太滑 | 换摩擦大的物块；查电量 |
| 动作抖 / 一顿一顿 | 同步推理跟不上 50 Hz | 已默认 RTC；可加大 `--interpolation_multiplier` |
| GPU OOM | GPU 0 被别人占 | `CUDA_VISIBLE_DEVICES=1` |
| `httpx.InvalidURL` | `no_proxy` 含 `[::1]` | 脚本已重写 `no_proxy` |
| 分词器 403 / GatedRepoError | `paligemma-3b-pt-224` 是 gated 仓 | 脚本已 `HF_HUB_OFFLINE=1` + 补 `.no_exist` 标记 |

---

## 8. 已验证 / 未验证

**已验证（不用再怀疑）**

| 项 | 结果 |
|---|---|
| 处理器流水线（rename / 相对-绝对配对 / 分词） | ✅ 通过（mask `[T,T,T,T,T,T,F]`，gripper 保持绝对） |
| 真实加载 8.8 GB + `predict_action_chunk` | ✅ `(1,50,7)`，`\|a[0]-state\|` 最大 **5.78**（阈值 30） |
| 服务器 ↔ 树莓派 ZMQ 协议（跨代码版本） | ✅ 握手 0.14–0.18 s |
| 观测 RTT | ✅ 中位 20–28 ms（时限 250 ms） |
| 两路相机 | ✅ 480×640×3 uint8，非零像素 99–100% |
| NVMe 加载耗时 | ✅ 阶段 A + 加载 + 前向 ≈ 2 分钟 |
| **抓取成功率** | ❌ **不行**。根因判断为**训练数据太少 / 质量不够**（30 条 episode），需补数据重训 |

**未验证**：真实相机视角与训练是否一致（靠 `videos/` 回放对比）、RTC 在真实负载下的稳定性。

---

## 9. 环境事实卡

**服务器（`pi05-server` / 10.23.14.66，user `yuntao`）**

| 项 | 值 |
|---|---|
| GPU | 2 × RTX 3090 Ti 24 GiB（**常被别人占，注意显存**） |
| conda | `lerobot_alohamini`（`~/miniconda3/envs/...`，lerobot 0.6.1，`pip -e`） |
| checkpoint | `outputs/train/pi05_am2_right_arm/checkpoints/last/pretrained_model`（= `040000`） |
| 训练数据 | `datasets/am2_right_arm_test`：**30 episodes / 16337 帧 / 30 fps**，任务串 `pickup1` |
| 磁盘 | `/dev/sdb5` 机械盘（ROT=1，6.6 MB/s）→ 必须用 NVMe `/tmp` |
| 内存 | 31 GB，多人共用 |

**树莓派（10.42.0.186，user `lab408`）**

| 项 | 值 |
|---|---|
| 仓库 / 环境 | `~/MyProject/lerobot_alohamini`，conda `lerobot_alohamini` |
| 总线别名 | `/dev/am_arm_follower_left`（左臂+升降）、`/dev/am_arm_follower_right`（右臂+底盘） |
| 相机别名 | `/dev/am_camera_forward`、`wrist_left`、`wrist_right`、`backward`、`chest` |
| 校准 | `~/.cache/huggingface/lerobot/calibration/robots/alohamini/AlohaMiniRobot.json` |

---

## 10. 文件清单（都在 `tests/yuntao/`）

| 文件 | 跑在哪 | 作用 |
|---|---|---|
| [`run_tunnel.sh`](run_tunnel.sh) | PC | 找 Pi → 查在线 → 查 host → 查端口 → 开隧道 |
| [`run_host_right_arm.sh`](run_host_right_arm.sh) | 树莓派 | 起 Host（`--daemon` / `--stop`） |
| [`run_pi05_eval.sh`](run_pi05_eval.sh) | 服务器 | 内存/目录/隧道预检 → 自检 → 探针 → 实机评估 |
| [`verify_pi05_inference.py`](verify_pi05_inference.py) | 服务器 | checkpoint 自检；`--full` 真跑一次前向 |
| [`probe_alohamini_link.py`](probe_alohamini_link.py) | 服务器 | **只读**链路探针，不动机械臂 |
| [`inspect_training_pose.py`](inspect_training_pose.py) | 服务器 | 算示教起始姿态（§4.2 表来源） |
| [`push_eval_dataset.sh`](push_eval_dataset.sh) | 服务器 | 把已跑完的数据集传 HF |
| [`run_pi05_train.sh`](run_pi05_train.sh) | 服务器 | 当初的微调脚本（评估参数出处） |
| [`pi05_推理代码导读.md`](pi05_推理代码导读.md) | — | 推理链路关键代码的阅读顺序 |
