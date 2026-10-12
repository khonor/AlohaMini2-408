# AGENT_GUIDE.md — 面向 AI 智能体与用户的 LeRobot 助手

本文件是一份实用、可直接复制粘贴的配套指南，供任何正在帮助用户使用 LeRobot 的 AI 智能体（Cursor、Claude、ChatGPT、Codex 等）参考。它补充 [`AGENTS.md`](./AGENTS.md)（面向开发者/贡献者的上下文），提供**面向用户的指导**：如何开始、训练什么、训练多久、如何录制，以及如何校准 SO-101。

---

## 1. 从这里开始——先询问用户（强制要求）

在给出任何命令建议之前，智能体必须先向用户提出至少以下问题，并等待回答：

1. **你的目标是什么？**（例如“教我的 SO-101 叠衣服”“在现有的 HF 数据集上训练一个策略”“提交一个 PR”“理解代码库”）
2. **你有哪些硬件？**
   - 机器人：无 / SO-100 / SO-101 / Koch / LeKiwi / Reachy / 其他
   - 遥操作：主臂 / 手机 / 键盘 / 手柄 / 无
   - 摄像头：多少个、分辨率、固定还是移动？
3. **你将在什么机器上训练？**
   - GPU 型号 + 显存（例如“笔记本 3060 6 GB”“RTX 4090 24 GB”“A100 80 GB”“仅 CPU”）
   - 操作系统：macOS / Linux / Windows
4. **你的技能水平与时间预算？** 第一次接触、有一些机器学习经验、还是资深用户？几小时、几天、还是一个周末？
5. **你是否已有数据集？** 有（HF 仓库 id？）/ 没有 / 想录制一个
6. **我现在能如何帮你？**（选一个具体的下一步）

只有在得到回答之后，才提出一条具体路径。如果有任何歧义，请再次询问，而不是猜测。请偏向于**对用户硬件和目标而言最简单且可行的方案**。

---

## 2. 60 秒了解 LeRobot

LeRobot = **数据集 + 策略 + 环境 + 机器人控制**，由少量强有力的抽象统一起来。

- **`LeRobotDataset`** — 感知 episode 的数据集（视频或图像 + 动作 + 状态），可从 Hub 或磁盘加载。
- **策略**（`ACT`、`Diffusion`、`SmolVLA`、`π0`、`π0.5`、`Wall-X`、`X-VLA`、`VQ-BeT`、`TD-MPC` 等）——全部继承 `PreTrainedPolicy`，并且可以从 Hub 推送/拉取。
- **处理器（Processors）** — 数据集 → 策略 → 机器人之间的可组合小型变换。
- **环境（仿真）** 与 **机器人（真机）** — 相同的动作/观测契约，因此代码可以干净地替换。
- **CLI** — `lerobot-record`、`lerobot-train`、`lerobot-eval`、`lerobot-teleoperate`、`lerobot-calibrate`、`lerobot-find-port`、`lerobot-setup-motors`、`lerobot-replay`。

关于仓库架构，请参见 [`AGENTS.md`](./AGENTS.md)。

---

## 3. 快速上手路径（选一条）

### 路径 A — “我有 SO-101，想要第一个训练好的策略”

前往 §4（SO-101 端到端），然后 §5（数据技巧），然后 §6（选择策略——多半是 **ACT**），然后 §7（训练多久），然后 §8（评估）。

### 路径 B — “我没有硬件，想用现有数据集训练”

跳过 §4。在 §6 中选择策略，在 §7 中选择时长，然后按 §4.9 运行 `lerobot-train`，使用 Hub 的 `--dataset.repo_id` 以及用于评估的 `--env.type`。最后完成 §8。

### 路径 C — “我只想理解代码库”

阅读上面的 §2，然后阅读 `AGENTS.md` 的“架构”部分，再打开 `src/lerobot/policies/act/` 和 `src/lerobot/datasets/lerobot_dataset.py` 作为典型示例。

---

## 4. SO-101 端到端速查表

完整细节见 [`docs/source/so101.mdx`](./docs/source/so101.mdx) 和 [`docs/source/il_robots.mdx`](./docs/source/il_robots.mdx)。以下是最少命令及其顺序。在执行前，请确认机械臂已组装并已通电。

**4.1 安装**

```bash
pip install 'lerobot[feetech]'              # SO-100/SO-101 电机驱动栈
# pip install 'lerobot[all]'                # 全部功能
# pip install 'lerobot[aloha,pusht]'        # 特定功能
# pip install 'lerobot[smolvla]'            # 添加 SmolVLA 依赖
git lfs install && git lfs pull
hf auth login                               # 推送数据集/策略所必需
```

贡献者也可以改用 `uv sync --locked --extra feetech`（参见 `AGENTS.md`）。

**4.2 查找 USB 端口** — 每条机械臂运行一次，按提示拔出设备。

```bash
lerobot-find-port
```

macOS：`/dev/tty.usbmodem...`；Linux：`/dev/ttyACM0`（可能需要 `sudo chmod 666 /dev/ttyACM0`）。

**4.3 设置电机 ID 与波特率**（一次性，每条机械臂各做一次）

```bash
lerobot-setup-motors --robot.type=so101_follower --robot.port=<FOLLOWER_PORT>
lerobot-setup-motors --teleop.type=so101_leader  --teleop.port=<LEADER_PORT>
```

**4.4 校准** — 将所有关节置于中位，按 Enter，然后让每个关节遍历其完整运动范围。`id` 是校准键——在所有地方都复用它。

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=<FOLLOWER_PORT> --robot.id=my_follower
lerobot-calibrate --teleop.type=so101_leader  --teleop.port=<LEADER_PORT>   --teleop.id=my_leader
```

**4.5 遥操作**（完整性检查，不录制）

```bash
lerobot-teleoperate \
  --robot.type=so101_follower --robot.port=<FOLLOWER_PORT> --robot.id=my_follower \
  --teleop.type=so101_leader  --teleop.port=<LEADER_PORT>  --teleop.id=my_leader \
  --robot.cameras="{ front: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}}" \
  --display_data=true
```

> **在 SO-100 / SO-101 上出现 Feetech 超时 / 通信错误？** 在改动软件之前，请检查菊花链上的**电机红色 LED**。
>
> - **全部常亮红色，从夹爪 → 底座链路** → 接线正常。
> - **一个或多个电机不亮 / 链路中途中断** → 接线问题：重新插紧 3 针线缆，检查控制板电源，并确保每个电机都完全插到位。
> - **LED 闪烁** → 电机处于**错误状态**：通常是过载（强行让关节超出其限位）**或电源电压不正确**。SO-100 / SO-101 有两种版本——**5 V / 7.4 V** 版本和 **12 V** 版本——二者不可互换。在 5 V / 7.4 V 的机械臂上使用 12 V 电源（反之亦然）会触发该错误；通电前请确认你的电机版本。
>
> 大多数“超时”错误是物理问题，而非代码问题。

**4.6 录制数据集** — 按键：**→** 下一个，**←** 重做，**ESC** 结束并上传。

```bash
HF_USER=$(NO_COLOR=1 hf auth whoami | awk -F': *' 'NR==1 {print $2}')

lerobot-record \
  --robot.type=so101_follower --robot.port=<FOLLOWER_PORT> --robot.id=my_follower \
  --teleop.type=so101_leader  --teleop.port=<LEADER_PORT>  --teleop.id=my_leader \
  --robot.cameras="{ front: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}}" \
  --dataset.repo_id=${HF_USER}/my_task \
  --dataset.single_task="<describe the task in one sentence>" \
  --dataset.num_episodes=50 \
  --dataset.episode_time_s=30 \
  --dataset.reset_time_s=10 \
  --display_data=true
```

**4.7 可视化** — 训练之前**务必**先做这一步。检查是否有丢帧、摄像头模糊、目标不可达、物体位置不一致等问题。
上传后：https://huggingface.co/spaces/lerobot/visualize_dataset → 粘贴 `${HF_USER}/my_task`。它适用于**任何 LeRobot 格式的 Hub 数据集**——可用它来浏览其他数据集、检查 episode 质量，或在重新训练前调试你自己的数据。

**4.8 回放一个 episode**（完整性检查）

```bash
lerobot-replay --robot.type=so101_follower --robot.port=<FOLLOWER_PORT> --robot.id=my_follower \
  --dataset.repo_id=${HF_USER}/my_task --dataset.episode=0
```

**4.9 训练**（默认：ACT——最快、内存占用最低）。Apple 芯片：`--policy.device=mps`。没有本地 GPU？加上 `--job.target=<flavor>`（例如 `a10g-small`，可用 `hf jobs hardware` 列出全部选项）来改为在 Hugging Face Jobs 上运行。策略与时长参见 §6/§7。

```bash
lerobot-train \
  --dataset.repo_id=${HF_USER}/my_task \
  --policy.type=act \
  --policy.device=cuda \
  --output_dir=outputs/train/act_my_task \
  --job_name=act_my_task \
  --batch_size=8 \
  --wandb.enable=true \
  --policy.repo_id=${HF_USER}/act_my_task
```

**4.10 在真机上评估** — 将成功率与遥操作基线进行比较。

```bash
lerobot-record \
  --robot.type=so101_follower --robot.port=<FOLLOWER_PORT> --robot.id=my_follower \
  --robot.cameras="{ front: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}}" \
  --dataset.repo_id=${HF_USER}/eval_my_task \
  --dataset.single_task="<same task description as training>" \
  --dataset.num_episodes=10 \
  --policy.path=${HF_USER}/act_my_task
```

---

## 5. 数据采集技巧（从新手到可靠策略）

好数据胜过聪明的模型。请采用以下默认做法，只有在有证据时才偏离它们。

### 5.1 装置与人体工学

- 在动软件之前，先**固定好支架和摄像头**。如果支架晃动或操作者感到别扭，先解决这些问题——更多糟糕的数据帮不上忙。
- **光照比分辨率更重要。** 使用漫射、稳定的光。避免移动的阴影。
- **“只看摄像头画面，你能完成这个任务吗？”** 如果不能，那你的摄像头布置有问题。在录制前修正。
- 可用时，为 rollout 启用**动作插值**，以获得更平滑的轨迹。

### 5.2 录制前先练习

- 不录制地做 5–10 次演示。形成一套刻意、可重复的策略。
- 犹豫或不一致的演示会让模型学会犹豫。

### 5.3 质量优先于速度

刻意、高质量的执行胜过快速潦草的操作。只有在策略已经稳定成型**之后**才去优化速度——绝不要为此牺牲质量。

### 5.4 episode 内部与 episode 之间的一致性

保持相同的抓取方式、接近向量和时序。连贯一致的策略比千变万化的动作容易学习得多。

### 5.5 从小处开始，再逐步扩展（黄金法则）

- **最初的 50 个 episode = 任务的受限版本**：一个物体、固定位置、固定摄像头布置、一位操作者。
- 训练一个快速的 ACT 模型。看看哪些地方失败。
- **然后一次沿一个维度增加多样性**：更多位置 → 更多光照 → 更多物体 → 更多操作者。
- 不要试图在第一天就采集出“完美数据集”。要迭代。

### 5.6 适合初学者的策略选择

- **笔记本 / 第一次尝试 / 想快速看到结果 → ACT。** 效果出奇地好，即使在笔记本 GPU 上也能快速训练。
- **更大的 GPU / 语言条件化 / 多任务 → SmolVLA。** 解冻视觉编码器（见 §7）在这里收益很大。
- 在你拥有已验证的 ACT 基线和 20+ GB 的 GPU 之前，先不要碰 π0 / π0.5 / Wall-X / X-VLA。

### 5.7 首个任务的推荐默认值

| 设置          | 取值                                                                                                                                                 |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Episode 数量         | 起步 **50**，首次训练后扩展到 100–300                                                                                                |
| Episode 时长   | 20–45 秒（抓取/放置类任务更短也可以）                                                                                                             |
| 复位时间       | 10 秒                                                                                                                                                  |
| FPS              | 30                                                                                                                                                    |
| 摄像头          | **推荐 2 个摄像头**：1 个固定前视 + 1 个腕部。多视角通常优于单视角。单个固定摄像头也可以，能让事情更简单。 |
| 任务描述 | 简短、具体、以动作表述的句子                                                                                                              |

### 5.8 排查信号

- 策略在某个特定阶段失败 → 针对**该阶段**再录制 10–20 个 episode。
- 策略抖动 / 震荡 → 很可能是演示不一致，或需要更多训练；重新录制最差的 episode（用 **←** 重做）。
- 策略忽略物体 → 是摄像头取景或光照问题，不是模型问题。

另见：[什么样的数据集才算好数据集](https://huggingface.co/blog/lerobot-datasets#what-makes-a-good-dataset)。

---

## 6. 我应该训练哪个策略？

将策略与用户的 **GPU 显存**和**时间预算**相匹配。下表中的数字来自一次内部性能分析运行（每个策略执行一次训练更新）。它们**仅供参考**——请查看注意事项。

### 6.1 性能分析快照（仅供参考）

所有策略通常训练 **5–10 个 epoch**（见 §7）。

> **面向人类的版本：** [计算硬件指南](./docs/source/hardware_guide.mdx) 复用了下面的表格，并补充了云 GPU 档位指南和 Hugging Face Jobs 的指引。

| 策略      | 批大小 | 更新耗时（ms） | 峰值 GPU 显存（GB） | 最适合                                                                                         |
| ----------- | ----: | ----------: | ----------------: | ------------------------------------------------------------------------------------------------ |
| `act`       |     4 |    **83.9** |          **0.94** | 初次使用者、笔记本、单任务。快速且可靠。                                       |
| `diffusion` |     4 |       168.6 |              4.94 | 多模态动作分布；需要中端 GPU。                                           |
| `smolvla`   |     1 |       357.8 |              3.93 | 语言条件化、多任务、小型 VLA。**解冻视觉编码器可获大幅收益**（见 §7）。 |
| `xvla`      |     1 |       731.6 |             15.52 | 大型 VLA、多任务。                                                                           |
| `wall_x`    |     1 |       716.5 |             15.95 | 带有世界模型目标的大型 VLA。                                                            |
| `pi0`       |     1 |       940.3 |             15.50 | 强大的大型 VLA 基线（Physical Intelligence）。                                               |
| `pi05`      |     1 |      1055.8 |             16.35 | 更新的 π 策略；占用与 `pi0` 相近。                                                      |

**关键注意事项：**

- **优化器：** 测量使用的是 **SGD**。LeRobot 的默认优化器是 **AdamW**，它会保留额外的优化器状态 → 使用默认设置时**峰值内存会明显更高**，对 `pi0`、`pi05`、`wall_x`、`xvla` 尤其如此。
- **批大小：** 大型策略是在批大小为 1 时进行性能分析的。实际使用中应取**更大的批大小**以保证训练稳定（见 §7.4）。内存大致随批大小线性增长。

### 6.2 决策规则

- **< 8 GB 显存（笔记本、3060、M 系列 Mac）：** → `act`。如果有约 6–8 GB 空闲显存，也可以考虑 `diffusion`。
- **12–16 GB 显存（4070/4080、A4000）：** → 使用默认设置的 `smolvla`，或使用更大批大小的 `act`/`diffusion`。`pi0`/`pi05`/`wall_x`/`xvla` 只有在小批大小 + 梯度累积时才可行。
- **24+ GB 显存（3090/4090/A5000）：** → 任意策略。多任务优先选 `smolvla`（已解冻）；单任务抓取-放置优先选 `act`（通常仍是性价比最高的选择）。也可以尝试 `pi0` 或 `pi05` 或 `xvla`
- **80 GB（A100/H100）：** → 任意策略，且可用健康的批大小。`pi05`、`xvla`、`wall_x` 会变得很从容。
- **仅 CPU：** → 不要在这里训练。使用 Google Colab（见 [`docs/source/notebooks.mdx`](./docs/source/notebooks.mdx)）或租用 GPU。

---

## 7. 我应该训练多久？

机器人模仿学习通常在数据集上迭代**几个 epoch** 就会收敛，而不是几十万个原始步数。请先考虑 **epoch**，再换算成步数。

### 7.1 经验法则

- **通常总计：5–10 个 epoch。** 从 5 开始，评估，再决定增加是否有效。
- 非常小的数据集（< 30 个 episode）可能需要略多的 epoch——但首先，**去采集更多数据**。
- 带有预训练视觉骨干的 VLA 通常比从头训练需要**更少**的 epoch。

### 7.2 步数 ↔ epoch 换算

```
total_frames     = sum of frames over all episodes      # 例如 50 个 episode × 30 fps × 30 秒 ≈ 45,000
steps_per_epoch  = ceil(total_frames / batch_size)
total_steps      = epochs × steps_per_epoch
```

`--batch_size=8` 时的示例：

| 数据集规模            |  帧数 | 每个 epoch 步数 | 5 个 epoch | 10 个 epoch |
| ----------------------- | ------: | ------------: | -------: | --------: |
| 50 个 episode × 30 秒 @ 30 fps  |  45,000 |        ~5,625 |      28k |       56k |
| 100 个 episode × 30 秒 @ 30 fps |  90,000 |       ~11,250 |      56k |      113k |
| 300 个 episode × 30 秒 @ 30 fps | 270,000 |       ~33,750 |     169k |      338k |

用 `--steps=<N>` 传入计算出的总步数；在中间检查点（`outputs/train/.../checkpoints/`）进行评估。

### 7.3 各策略的起始点（单任务，约 50 个 episode）

| 策略         | 批大小 | 步数（首次运行） | 说明                                                             |
| -------------- | ----: | ----------------: | ----------------------------------------------------------------- |
| `act`          |  8–16 |           30k–80k | 单任务通常在 50k 以内收敛。                      |
| `diffusion`    |  8–16 |          80k–150k | 比 ACT 更能从更长训练中获益。                           |
| `smolvla`      |   4–8 |           30k–80k | 预训练 VLM → 收敛快。                                  |
| `pi0` / `pi05` |   1–4 |           30k–80k | 受显存限制；请用梯度累积让有效批大小 ≥ 16！ |

### 7.4 批大小指导

- 在遥操作数据上，**更大的批大小更可取**，可获得稳定的梯度。
- 如果 GPU 显存是瓶颈，请使用**梯度累积**来提高_有效_批大小，而不提高峰值内存。
- **学习率**随批大小温和缩放；对于 2–4 倍的批大小变化，大多数 LeRobot 默认值都能正常工作。

### 7.5 随 `--steps` 缩放 LR 调度与检查点

LeRobot 的默认调度器（例如 SmolVLA 的余弦衰减）使用 `scheduler_decay_steps=30_000`，这是为长时间训练运行设定的。当你缩短训练时（例如在小数据集上训练 5k–10k 步），请**相应地调小调度器**——否则学习率会一直停留在峰值附近，永远不衰减。检查点频率同理。

```bash
lerobot-train ... \
  --steps=5000 \
  --policy.scheduler_decay_steps=5000 \
  --save_freq=5000
```

经验法则：设置 `scheduler_decay_steps ≈ steps`，并把 `save_freq` 设为你想要的评估粒度（例如每 1k–5k 步）。如果你的运行非常短，请按比例匹配 `scheduler_warmup_steps`。

### 7.6 SmolVLA：解冻视觉编码器以获取真正收益

SmolVLA 自带 `freeze_vision_encoder=True`。解冻通常能在专门任务上**显著提升性能**，代价是更多显存和更慢的步速。启用方式：

```bash
lerobot-train ... --policy.type=smolvla \
  --policy.freeze_vision_encoder=false \
  --policy.train_expert_only=false
```

### 7.7 停止 / 继续训练的信号

- 训练损失进入平台期 → 停止，保存一个 Hub 检查点。
- 训练损失仍在下降，且你还不到 10 个 epoch → 继续训练。

---

## 8. 评估与基准

两类评估：

### 8.1 真机评估（SO-101 等）

复用 `lerobot-record` 并配合 `--policy.path`，在真机上运行训练好的策略，并将该次运行保存为评估数据集。约定：数据集以 `eval_` 开头。

```bash
lerobot-record \
  --robot.type=so101_follower --robot.port=<FOLLOWER_PORT> --robot.id=my_follower \
  --robot.cameras="{ front: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}}" \
  --dataset.repo_id=${HF_USER}/eval_my_task \
  --dataset.single_task="<same task description used during training>" \
  --dataset.num_episodes=10 \
  --policy.path=${HF_USER}/act_my_task
```

报告各 episode 的成功率。与遥操作基线以及更早的检查点进行比较，以捕捉性能回退。

### 8.2 仿真基准评估

对于在仿真数据集（PushT、Aloha、LIBERO、MetaWorld、RoboCasa 等）上训练的策略，使用 `lerobot-eval` 并指定匹配的 `env.type`：

```bash
lerobot-eval \
  --policy.path=${HF_USER}/diffusion_pusht \
  --env.type=pusht \
  --eval.n_episodes=50 \
  --eval.batch_size=10 \
  --policy.device=cuda
```

- 对于本地检查点，使用 `--policy.path=outputs/train/.../checkpoints/<step>/pretrained_model`。
- 为获得稳定的成功率估计，`--eval.n_episodes` 应 ≥ 50。
- 可用的仿真环境位于 `src/lerobot/envs/`。具体基准请参见 [`docs/source/libero.mdx`](./docs/source/libero.mdx)、[`metaworld.mdx`](./docs/source/metaworld.mdx)、[`robocasa.mdx`](./docs/source/robocasa.mdx)、[`vlabench.mdx`](./docs/source/vlabench.mdx)。
- 要添加新基准，请参见 [`docs/source/adding_benchmarks.mdx`](./docs/source/adding_benchmarks.mdx) 和 [`envhub.mdx`](./docs/source/envhub.mdx)。

### 8.2b 用于基准评估的 Dockerfile

基准环境带有一些在本地安装起来很痛苦的底层依赖。仓库为每个受支持的基准都提供了**预先构建好的 Dockerfile**——用它们在可复现的环境中运行 `lerobot-eval`：

| 基准   | Dockerfile                                                                             |
| ----------- | -------------------------------------------------------------------------------------- |
| LIBERO      | [`docker/Dockerfile.benchmark.libero`](./docker/Dockerfile.benchmark.libero)           |
| LIBERO+     | [`docker/Dockerfile.benchmark.libero_plus`](./docker/Dockerfile.benchmark.libero_plus) |
| MetaWorld   | [`docker/Dockerfile.benchmark.metaworld`](./docker/Dockerfile.benchmark.metaworld)     |
| RoboCasa    | [`docker/Dockerfile.benchmark.robocasa`](./docker/Dockerfile.benchmark.robocasa)       |
| RoboCerebra | [`docker/Dockerfile.benchmark.robocerebra`](./docker/Dockerfile.benchmark.robocerebra) |
| RoboMME     | [`docker/Dockerfile.benchmark.robomme`](./docker/Dockerfile.benchmark.robomme)         |
| RoboTwin    | [`docker/Dockerfile.benchmark.robotwin`](./docker/Dockerfile.benchmark.robotwin)       |
| VLABench    | [`docker/Dockerfile.benchmark.vlabench`](./docker/Dockerfile.benchmark.vlabench)       |

构建并运行（请根据你的基准进行调整）：

```bash
docker build -f docker/Dockerfile.benchmark.robomme -t lerobot-bench-robomme .
docker run --gpus all --rm -it \
  -v $HOME/.cache/huggingface:/root/.cache/huggingface \
  lerobot-bench-robomme \
  lerobot-eval --policy.path=<your_policy> --env.type=<env> --eval.n_episodes=50
```

基础镜像的细节请参见 [`docker/README.md`](./docker/README.md)。

### 8.3 目标成功率

在 50 个干净 episode 的单任务抓取-放置中：ACT 在训练配置上应达到 **> 70% 成功率**。更低 → 是数据问题（见 §5），不是模型问题。泛化到新位置时预期会有下降——通过增加 episode 或多样性来恢复。

---

## 9. 延伸阅读与资源

- **入门：** [`installation.mdx`](./docs/source/installation.mdx) · [`il_robots.mdx`](./docs/source/il_robots.mdx) · [什么样的数据集才算好数据集](https://huggingface.co/blog/lerobot-datasets)
- **各策略文档：** 浏览 [`docs/source/*.mdx`](./docs/source/)（策略、硬件、基准、进阶训练）。
- **社区：** [Discord](https://discord.com/invite/s3KuuzsPFb) · [Hub `LeRobot` 标签](https://huggingface.co/datasets?other=LeRobot) · [数据集可视化工具](https://huggingface.co/spaces/lerobot/visualize_dataset)

> 请保持本文件是最新的。如果你总结出一条能避免某类用户错误的规则，请把它补充到这里以及 [`AGENTS.md`](./AGENTS.md) 中。
