# AM2 pi0.5 微调训练 —— 文件 / 代码 / 产物 / 数据来源 汇总说明

> 记录时间：2026-10-11 15:30（训练仍在进行中）
> 训练任务：`lerobot/pi05_base` 在自采数据集 `khonor/am2_right_arm_test` 上做右侧单臂微调
> 运行脚本：`run_pi05_train.sh`，日志：`train_pi05.log`
> 状态快照：**20,000 / 40,000 step（50%）**，已存 step 10000、20000 两个 checkpoint，loss 从 step50 的 1.69 降到 0.07~0.33，速度 ~1.43 step/s，显存 12.87 GB，无任何 ERROR / Traceback。

本文档对应 4 个问题，所有结论均由仓库文件、训练日志、HF 缓存和 `nvidia-smi` / `ps` 实测得出，未做推测。

---

## 0. 训练环境与本次运行参数（先对齐事实）

| 项 | 值 |
|---|---|
| 项目根目录 | `/data/home/yuntao/MyProject/2_Alohamini2` |
| 代码目录 | `/data/home/yuntao/MyProject/2_Alohamini2/lerobot_alohamini`（git 仓 `github.com/liyiteng/lerobot_alohamini`，HEAD `ec65318f`，`git status` 干净） |
| Python 环境 | conda `lerobot_alohamini`（Python 3.12），`lerobot` 以 **editable 方式**指向 `lerobot_alohamini/src`（`site-packages/__editable__.lerobot-0.6.1.pth`），因此训练实际导入的就是这个文件夹里的源码 |
| 依赖版本 | torch 2.10.0，transformers 5.5.4 |
| GPU | `CUDA_VISIBLE_DEVICES=0`，RTX 3090 Ti 24 GiB；实测训练进程 PID 1307617 占用 GPU0 14110 MiB |
| 训练进程 | PID 1307615(`run_pi05_train.sh`) → 1307617(`lerobot-train`)，启动时间 10:10，仍在运行 |
| 关键超参 | `steps=40000`、`batch_size=2`、`lr=2.5e-5`（cosine，warmup 500 / decay 40000）、`save_freq=10000`、`dtype=bfloat16`、`gradient_checkpointing=true`、`train_expert_only=true`、`use_relative_actions=true`、`wandb.enable=false`、`save_checkpoint_to_hub=false` |

时序（来自 `train_pi05.log`，已剔除进度条噪音）：

| 时间 | 事件 |
|---|---|
| 10:10:18 | `Creating policy`（构建空模型结构，耗时约 10 分钟） |
| 10:20:29 | `Enabled gradient checkpointing for PI05Pytorch model` |
| 10:20:29 | `Loading model from: lerobot/pi05_base` → `✓ Loaded state dict from model.safetensors` |
| 10:25:44 | `Remapped 812 state dict keys` / `All keys loaded successfully!`（另打印 2 条 vision embedding 键的 WARNING，属正常提示） |
| 10:49:49 | 优化器/调度器创建完成，`num_learnable_params=693422112 (693M)`、`num_total_params=4143404816 (4.14B)` |
| 11:11:38 | 首个日志点 `step:50 loss:1.692` |
| 13:08:56 | `Checkpoint policy after step 10000` |
| 15:20:18 | `Checkpoint policy after step 20000`（文档记录时该 ckpt 正在落盘） |

即：**10:10 启动 → 10:20~10:26 加载基座权重 → 10:49 进入训练循环 → 11:11 打出第一个 loss**。前面近 40 分钟都是模型/处理器构建与权重加载，这也是为什么要专门写一个秒级健康检查脚本（见 1.3）。

---

## 1. 在其他对话任务中新建的文件及作用

全部新建文件都落在**项目根目录**（不在 `lerobot_alohamini` git 仓内，所以 `git status` 看不到），清单如下：

| 文件 | 大小 / 最后修改 | 作用 |
|---|---|---|
| `check_pi05_env.py` | 1.7 KB · 10-11 11:01 | **秒级环境健康检查**。只做两件过去会失败的事：① `AutoTokenizer.from_pretrained("google/paligemma-3b-pt-224")`；② `make_pre_post_processors(PI05Config(), "lerobot/pi05_base")`。刻意**不加载 14.5 GB 权重**，几秒出结果而不是 20 分钟；成功标志是打印 `ALL CHECKS PASSED`。用于长训练前快速验证 gated repo / no_proxy 两个坑是否仍然修好。 |
| `resume_pi05_download.sh` | 1.9 KB · 10-10 19:18 | **可断点续传地下载 `lerobot/pi05_base` 的 `model.safetensors`（14,467,165,872 字节）**。因为 huggingface_hub 1.x 不支持续传（每次进程生成新的 uuid `.incomplete`），脚本改用 `curl -L -C -` 直接续传 LFS 文件，用固定 sha256 `0eb11ca9…59b0f` 校验后 `mv` 进 HF blob 缓存（`~/.cache/huggingface/hub/models--lerobot--pi05_base/blobs/`）。共 300 次重试循环，是最耗时一步（14 GB）的兜底方案。 |
| `run_pi05_train.sh` | 3.4 KB · 10-11 11:01 | **唯一的训练启动脚本**（`lerobot-train` 的完整命令行 + 环境变量）。文件头注释记录了三个必须保留的修复：① **gated repo 403**：`google/paligemma-3b-pt-224` 是 gated 模型，transformers 5.x 会无条件探测仓库里根本不存在的 `chat_template.jinja`，gated 仓库对任意路径都返回 403 而不是 404，于是抛 `GatedRepoError`；脚本在 HF 缓存里写入 Hub 自己的 `.no_exist/<commit>/chat_template.jinja` 标记，让 tokenizer 100% 走本地缓存（幂等、不联网）。② **`no_proxy` 不能含 `[::1]`**，httpx 解析该 URL 模式会 `InvalidURL` 崩溃，脚本重写为干净的 `localhost,127.0.0.1,::1,…`。③ **显存预算**：3.62 B 全参数 fp32=14.5 GB，全量微调需 参数6.7 + 梯度6.7 + AdamW m,v 27.0 ≈ 40.5 GiB，24 GiB 不可能；`train_expert_only=true` 冻结 2.93 B PaliGemma VLM，只训 0.69 B action expert。另外脚本还带了 `--rename_map`（把数据集相机键映射到 pi0.5 期望键）、`--policy.path=lerobot/pi05_base`、`--dataset.root` 指向本地数据等。 |
| `train_pi05.log` | 1.5 MB · 仍在增长 | 本次训练的**stdout/stderr 全量日志**（`tqdm` 进度 + `ot_train.py` 指标行）。用于复盘加载耗时、loss 曲线、checkpoint 落盘时间，也是本文档时序表的依据。 |
| `datasets/am2_right_arm_test/` | 379 MB · 10-10 17:52 | 用 `snapshot_download(..., local_dir=…)` 从 HF 数据集仓 `khonor/am2_right_arm_test` **完整下载到本地的副本**（30 episodes / 16,337 frames / 1 个 task，2 路 640×480 AV1 视频）。训练时 `--dataset.root` 直接读它。带有 `.cache/huggingface/download/*.metadata`（哈希 + 时间戳 1791625933 ≈ 10-10 17:52）作为下载凭据。 |

> 另外 HF 缓存 `~/.cache/huggingface/hub/models--google--paligemma-3b-pt-224/.no_exist/2c7ebe1a…/chat_template.jinja`（0 字节，10-11 09:50）是 `run_pi05_train.sh` 运行时自动补的"文件不存在"标记，不是人写的脚本，但属于本次训练留下的关键修复产物。

**未留在磁盘的临时文件**：`resume_pi05_download.sh` 的下载中间文件 `pi05_base.model.safetensors.part` 已在校验通过后被 `mv` 走，现不存在。

---

## 2. 训练主要涉及的 `lerobot_alohamini` 中代码

> 重要区分：训练**不经过任何 alohamini 硬件代码**。`src/lerobot/robots/alohamini/`、`lerobot_record.py`、`create_alohamini_visual_only_dataset.py` 等只用于**采数据**（生成上面的数据集），pi0.5 微调走的是 lerobot 通用训练栈 + PI05 policy 栈。
> 另外这些源码的 mtime 都是 10-10 11:59（仓库 checkout 时间），本次训练**没有改动过一行源码**；editable install 使得 `lerobot-train` 直接运行这些文件。

### 2.1 训练入口与训练循环

| 文件 | 关键位置 | 作用 |
|---|---|---|
| `src/lerobot/scripts/lerobot_train.py` | `train()`:178、`make_policy()`:293、`save_checkpoint()`:653、`main()`:766 | 训练主流程：解析配置 → 建数据集 → 建 policy → 建 processor → 建优化器/调度器 → 训练循环 → 定期存 checkpoint。日志里的 `ot_train.py` 就是它。 |
| `src/lerobot/configs/train.py` | 165–172 行 `PreTrainedConfig.from_pretrained(policy_path)` 并回填 `pretrained_path` | `--policy.path=lerobot/pi05_base` 的落地处：先从基座仓读 `config.json` 得到 PI05Config，再把 `pretrained_path` 设为该路径/仓 id（日志里 `'pretrained_path': 'lerobot/pi05_base'`）。 |

### 2.2 PI05 policy 本体（本次微调的核心）

| 文件 | 关键位置 | 作用 |
|---|---|---|
| `src/lerobot/policies/pi05/configuration_pi05.py` | `class PI05Config`:30；`train_expert_only`:88；`use_relative_actions` / `relative_exclude_joints`；`paligemma_variant="gemma_2b"` / `action_expert_variant="gemma_300m"`；`max_state_dim/max_action_dim=32`；`chunk_size/n_action_steps=50`；quantile 归一化映射 | pi0.5 的全部超参定义。本次用的 `train_expert_only=true`、`use_relative_actions=true`（gripper 除外）都在这里。 |
| `src/lerobot/policies/pi05/modeling_pi05.py` | `class PaliGemmaWithExpertModel`:344、`train_expert_only`:357/434/443（冻结 VLM 分支）、`class PI05Pytorch`:563、`forward()`:742/1255、gradient checkpointing 日志来自 `:611` | π₀.5 网络：PaliGemma VLM（含 SigLIP vision tower）+ Gemma-300M action expert 的 flow-matching 实现（openpi 的移植）。`train_expert_only` 在此决定只解冻 action expert，是能在 24 GB 上跑起来的关键。 |
| `src/lerobot/policies/pi05/processor_pi05.py` | `make_pi05_pre_post_processors()`:100；`TokenizerProcessorStep(tokenizer_name="google/paligemma-3b-pt-224")`:152；`RelativeActionsProcessorStep` / `Normalizer` / `Pi05PrepareStateTokenizerProcessorStep`；`AbsoluteActionsProcessorStep` | 前/后处理流水线：raw→relative→quantile 归一化→state 分词→**PaliGemma tokenizer**→上 device；输出侧 unnormalize→还原为绝对动作。`google/paligemma-3b-pt-224` 就是在这里被触发下载/读缓存的（所以 gated repo 的坑出现在这一步）。 |
| `src/lerobot/policies/factory.py` | `make_policy_config()`:192（`pi05 → PI05Config`:225）、`make_pre_post_processors()`:280（`:420` 分派到 pi05）、`make_policy()`:531（`:137` 注册 `PI05Policy`） | 把 `--policy.type/--policy.path` 变成实例化好的 policy 与 processor。 |
| `src/lerobot/policies/pretrained.py` | `:357` `"pi05": "lerobot/pi05_base"` | 生成模型卡时声明"微调自 lerobot/pi05_base"（base_model 字段）。 |
| `src/lerobot/policies/__init__.py` | `:30` 导出 `PI05Config` | PI05 的注册与导出。 |

### 2.3 数据、优化器、Checkpoint 基础设施

| 文件 | 关键位置 | 作用 |
|---|---|---|
| `src/lerobot/datasets/factory.py` | `make_dataset()`:69、`make_train_eval_datasets()`:139、`:98/:122/:191/:203` 透传 `video_backend` | 按 `--dataset.repo_id` + `--dataset.root` 载入 LeRobotDataset，`--dataset.video_backend=pyav` 在此生效。 |
| `src/lerobot/datasets/lerobot_dataset.py`、`sampler.py` | — | 帧读取/视频解码、EpisodeAwareSampler 与 resume 采样状态（日志 `data_s:0.003~0.017` 就是取数耗时）。 |
| `src/lerobot/optim/factory.py` | — | 按 `use_policy_training_preset` 建 AdamW（β 0.9/0.95, wd 0.01）+ `CosineDecayWithWarmup`（peak 2.5e-5 → decay 2.5e-6）。 |
| `src/lerobot/common/train_utils.py` | `get_step_checkpoint_dir()`:49、`save_checkpoint()`:93（目录结构注释 107–121）、`load_training_state()`:201、`push_checkpoint_to_hub()`:290 | 决定 `checkpoints/<8位step>/pretrained_model` + `training_state` 的落盘结构（第 3 节的产物布局即由它定义），以及 `last` 软链。 |
| `src/lerobot/configs/policies.py` | `pretrained_path`:81、`push_to_hub`:70 | 通用 policy 配置字段（`pretrained_path` 指向基座，`push_to_hub` 控制模型卡/上传行为）。 |
| `src/lerobot/utils/constants.py` | `:47` `CHECKPOINTS_DIR`、`:49` `PRETRAINED_MODEL_DIR`、`:50` `TRAINING_STATE_DIR` | 上述目录名常量。 |

### 2.4 只用于采数据、与本次训练无关的 alohamini 代码（备查）

`src/lerobot/robots/alohamini/`（`alohamini.py` / `alohamini_client.py` / `alohamini_host.py` / `camera_stream.py` / `model_specs.py` / `config_alohamini.py` / `alohamini_calibrate.py`）、`src/lerobot/scripts/lerobot_record.py`、`src/lerobot/scripts/lerobot_rollout.py`、`src/lerobot/scripts/create_alohamini_visual_only_dataset.py`、根目录 `alohamini_sim/`。它们是数据集 `khonor/am2_right_arm_test` 的来源（`robot_type: alohamini_client`），但不是 pi0.5 训练链路的一部分。

---

## 3. 训练产物在哪里、各是什么

产物全部在 `lerobot_alohamini/outputs/`（该目录被 `.gitignore:167` 的 `outputs/` 忽略，所以不会进 git）。

```
lerobot_alohamini/outputs/train/pi05_am2_right_arm/
└── checkpoints/
    ├── last -> 010000            # 软链，指向最新“完整”checkpoint（update_last_checkpoint 生成）
    ├── 010000/                   # 11 GB，step 10000 完整快照
    │   ├── pretrained_model/     # ← 可直接被加载推理/续训的模型目录
    │   │   ├── config.json                                  2.7 KB   PI05Config（输入/输出特征、chunk_size、dtype…）
    │   │   ├── model.safetensors                            9.35 GB  step10000 权重
    │   │   ├── train_config.json                            7.6 KB   本次训练全部超参 + 数据集信息（可用来 resume）
    │   │   ├── policy_preprocessor.json / policy_postprocessor.json   处理器流水线定义（步骤与顺序）
    │   │   ├── policy_preprocessor_step_3_normalizer_processor.safetensors  7.7 KB  数据集 quantile 归一化统计量
    │   │   └── policy_postprocessor_step_0_unnormalizer_processor.safetensors 7.7 KB  反归一化统计量
    │   └── training_state/       # ← 仅用于精确续训
    │       ├── optimizer_state.safetensors                  2.20 GB  AdamW 动量/方差
    │       ├── optimizer_param_groups.json                  14 KB    参数分组与 lr
    │       ├── rng_state.safetensors                        15 KB    RNG 状态（保证可复现）
    │       ├── scheduler_state.json                         256 B    cosine 调度器进度
    │       └── training_step.json                           66 B     当前 step(+ world size / batch size)
    └── 020000/                   # 记录时正在写：10-11 15:20 生成，暂只有 pretrained_model/config.json
                                  # 完成后结构与 010000 相同（约 11 GB）
```

另有训练过程日志 `2_Alohamini2/train_pi05.log`（1.5 MB，持续增长）——严格说是"运行日志"而非模型产物，但它是唯一的训练现场记录。

几个容易误解的点：

1. **`pretrained_model/` 这个名字是 lerobot 的固定目录名**，在 checkpoint 里它装的是**微调后的**权重，不是原始基座。
2. `model.safetensors` 9.35 GB 是"冻结 VLM + 可训 action expert"的完整模型（bfloat16 存盘；4.14 B 参数里 693 M 可训练）。
3. **`training_state/` 只有 010000 有**，因为它标记 `last`；每个 checkpoint 都会写，只是 020000 当时尚未写完。
4. 本次 `save_checkpoint_to_hub=false`、`wandb.enable=false`，所以**没有自动上传 HF、没有 wandb 记录**。脚本里 `--policy.repo_id=khonor/pi05_am2_right_arm` 配 `push_to_hub=true`，但自动上传发生在"训练正常结束"的收尾阶段；此刻磁盘上的 checkpoint 尚未推到 `khonor/pi05_am2_right_arm`。若要手动上传，可用 `huggingface-cli upload khonor/pi05_am2_right_arm <ckpt>/pretrained_model`。
5. 目录命名规则来自 `get_step_checkpoint_dir`：step 补零到与 `steps` 同宽（40000 → 5 位），所以是 `010000` / `020000` / 预期 `030000`、`040000`。

---

## 4. 数据集来源 与 pi0.5 基座模型的使用时机

### 4.1 数据集：是"你的 HF 仓 + 本地副本"

- 训练只用一个数据集：`--dataset.repo_id=khonor/am2_right_arm_test`（**你的 HF 数据集仓**），`--dataset.root=/data/home/yuntao/MyProject/2_Alohamini2/datasets/am2_right_arm_test`。
- 它不是训练时才从网上拉的：10-10 17:52 已用 `local_dir` 方式把整仓下载到项目内（379 MB，30 episodes / 16,337 frames / 1 task，2 路 640×480 视频，`robot_type=alohamini_client`，fps=30，state/action 均为 7 维右臂关节 + 夹爪；下载凭据见 `.cache/huggingface/download/*.metadata`，commit `f1d05577f430025901e129b11bf60e27a9a3cbf9`）。
- 训练时按 `root` 读本地 parquet/mp4，所以**训练期间不需要联网拉数据**；`repo_id` 主要用于元信息与模型卡引用。HF hub 缓存里对应目录只有 `refs/main`，没有 blob，与"下载到 `local_dir`"的方式一致。
- 相机键做了重命名映射：`observation.images.forward → observation.images.base_0_rgb`、`observation.images.wrist_right → observation.images.right_wrist_0_rgb`，以匹配 pi0.5 预训练的相机命名（配置里还有一个 `observation.images.left_wrist_0_rgb` 空槽，模型按 3 路相机结构定义）。

### 4.2 基座预训练模型：`lerobot/pi05_base`（HuggingFace 官方 lerobot 组织，不是你的仓）

| 问题 | 事实 |
|---|---|
| 是什么 | π₀.5（pi0.5）官方基座：`lerobot/pi05_base`，3.62 B 参数 fp32 共 14.5 GB，revision `22c30e5b34f9e7ab738b1b34fc2fc5ecfe2d28f0` |
| 什么时候下载 | **2026-10-10，分两步**：18:10 先下 `config.json` / `policy_preprocessor.json` / `policy_postprocessor.json` / `README.md` 等小文件并解析仓库（写 `refs/main`）；14.5 GB 的 `model.safetensors` 因 huggingface_hub 不能续传，改用 `resume_pi05_download.sh` 的 `curl -C -` 续传，**19:31 落盘、19:33 校验 sha256 通过并安装进 HF blob 缓存**（`blobs/0eb11ca9…`）。 |
| 附带下载 | 其 tokenizer 来自 `google/paligemma-3b-pt-224`，10-10 18:48 下载完成（`tokenizer.json` 34.6 MB 等）；因为该仓是 gated（当前账号无权限，403），10-11 09:50 由 `run_pi05_train.sh` 补写了 `.no_exist/.../chat_template.jinja` 标记，使其 100% 从缓存解析、不再触网。 |
| 什么时候被调用（本次训练） | 10-11 **10:10:18** `Creating policy`：`PreTrainedConfig.from_pretrained("lerobot/pi05_base")` 读 `config.json` 搭出 PI05Config 并设 `pretrained_path`；**10:20:29** 打印 `Loading model from: lerobot/pi05_base`，随后 `from_pretrained` 用**本地缓存**的 14.5 GB `model.safetensors` 灌权重（`✓ Loaded state dict`、`Remapped 812 state dict keys`）；**10:25:44** `All keys loaded successfully!`。整个过程**没有重新下载**——缓存已在 10-10 备好，因此训练启动不需要外网（除 gated 仓探测问题已用 `.no_exist` 标记规避）。 |
| 训练期间还会用到基座吗 | 会，但只读缓存：`processor_pi05.py:152` 的 `google/paligemma-3b-pt-224` tokenizer 在整个训练过程中负责把语言指令（"Task: …; Action: "）与 state 一起编码成 prompt。 |
| 显存折衷 | 14.5 GB 权重 + 优化器状态远超 24 GB，故 `train_expert_only=true` 冻结 2.93 B PaliGemma VLM，只训练 0.69 B action expert（日志：`num_learnable_params=693,422,112` / `num_total_params=4,143,404,816`），实测显存 12.87 GB。 |

### 4.3 一句话总结第 4 问

**训练数据 100% 来自你的 HF 数据集仓 `khonor/am2_right_arm_test`（且用的是 10-10 下载到项目内的本地副本）；pi0.5 基座 `lerobot/pi05_base` 是 HuggingFace lerobot 官方的预训练模型，于 10-10 18:10~19:33 下载进 HF 缓存，在 10-11 本次训练启动时（10:10 读配置、10:20 灌权重、10:25 完成）被加载并冻结其 VLM 部分，仅微调 action expert。**

---

## 附：复现/运维速查

```bash
# 0) 环境
conda activate lerobot_alohamini
cd /data/home/yuntao/MyProject/2_Alohamini2

# 1) 长训练前的秒级自检（应输出 ALL CHECKS PASSED）
python check_pi05_env.py

# 2) 启动微调（当前这个脚本已在跑；重复执行会另起一个训练进程，注意 GPU 占用）
bash run_pi05_train.sh

# 3) 观察进度 / 确认进程与 GPU
tail -f train_pi05.log
ps -p 1307617 -o pid,etime,cmd
nvidia-smi --query-compute-apps=pid,used_memory --format=csv

# 4) 查看 checkpoint
ls -la lerobot_alohamini/outputs/train/pi05_am2_right_arm/checkpoints/
#    last 软链指向最新完整 checkpoint；每个 <step>/pretrained_model 可直接喂给 lerobot 推理/续训

# 5) 续训（示例，--config_path 指向某 checkpoint 的 train_config.json）
# lerobot-train --config_path=<ckpt>/pretrained_model/train_config.json --resume=true
```
