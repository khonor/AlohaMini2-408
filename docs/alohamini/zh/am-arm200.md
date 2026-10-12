# AM-ARM200 — 完整工作流程

> **前置条件：** 先完成 [install.md](install.md)（安装与配置）。
> **硬件配置档：** 见 [profiles.md](profiles.md)。

单臂方案 —— 只需一台 PC，无需树莓派。

---

## 1. 端口配置

先插入主手臂（leader），运行查找工具，记下端口。然后插入从手臂（follower），记下它的端口：

```bash
lerobot-find-port
# 或者直接查看：
ls /dev/ttyACM*
```

> 重新插拔或重启后端口号可能变化。在接入下一个设备之前，先记录好每个端口路径。

## 2. 摄像头配置

```bash
lerobot-find-cameras
```

记下每个摄像头的索引。每个摄像头占用一个 USB 端口 —— 多个摄像头不要共用同一个 USB 集线器。

---

## 3. 校准

### 校准主手臂（leader）

```bash
lerobot-calibrate \
  --teleop.type=so101_leader \
  --teleop.port=/dev/ttyACM0 \
  --teleop.id=my_leader \
  --teleop.arm_profile=am-leader-6dof
```

### 校准从手臂（follower）

```bash
lerobot-calibrate \
  --robot.type=so101_follower \
  --robot.port=/dev/ttyACM1 \
  --robot.id=my_follower \
  --robot.arm_profile=am-follower-6dof
```

> AM-ARM200 Pro：将 `am-follower-6dof` 替换为 `am-follower-6dof-hd`。

校准完成后，请将两条手臂断电重启，改动才会生效。

---

## 4. 遥操作（Teleoperation）

```bash
lerobot-teleoperate \
  --teleop.type=so101_leader \
  --teleop.port=/dev/ttyACM0 \
  --teleop.id=my_leader \
  --teleop.arm_profile=am-leader-6dof \
  --robot.type=so101_follower \
  --robot.port=/dev/ttyACM1 \
  --robot.id=my_follower \
  --robot.arm_profile=am-follower-6dof
```

用手拖动主手臂 —— 从手臂会实时跟随镜像运动。

---

## 5. 数据集录制

录制前请确认两条手臂都已连接并完成校准。摄像头索引来自 `lerobot-find-cameras`（见 §2）。

新建数据集：

```bash
lerobot-record \
  --robot.type=so101_follower \
  --robot.port=/dev/ttyACM1 \
  --robot.id=my_follower \
  --robot.arm_profile=am-follower-6dof \
  --robot.cameras="{cam_wrist: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}, cam_top: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30}}" \
  --teleop.type=so101_leader \
  --teleop.port=/dev/ttyACM0 \
  --teleop.id=my_leader \
  --teleop.arm_profile=am-leader-6dof \
  --dataset.repo_id=$HF_USER/am_arm_test \
  --dataset.num_episodes=50 \
  --dataset.fps=30 \
  --dataset.episode_time_s=45 \
  --dataset.reset_time_s=8 \
  --dataset.single_task "pickup1" \
  --display_data=true
```

续录已有数据集 —— `--dataset.root` 指定本地工作目录；如果该路径尚不存在，LeRobot 会自动从 Hub 下载已有数据集的元数据：

```bash
lerobot-record \
  --robot.type=so101_follower \
  --robot.port=/dev/ttyACM1 \
  --robot.id=my_follower \
  --robot.arm_profile=am-follower-6dof \
  --robot.cameras="{cam_wrist: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30}, cam_top: {type: opencv, index_or_path: 1, width: 640, height: 480, fps: 30}}" \
  --teleop.type=so101_leader \
  --teleop.port=/dev/ttyACM0 \
  --teleop.id=my_leader \
  --teleop.arm_profile=am-leader-6dof \
  --dataset.repo_id=$HF_USER/am_arm_test \
  --dataset.root=$HOME/lerobot_datasets/$HF_USER/am_arm_test \
  --dataset.num_episodes=50 \
  --dataset.fps=30 \
  --dataset.episode_time_s=45 \
  --dataset.reset_time_s=8 \
  --dataset.single_task "pickup1" \
  --display_data=true \
  --resume=true
```

> AM-ARM200 Pro：将 `am-follower-6dof` 替换为 `am-follower-6dof-hd`。
> 摄像头名称（`cam_wrist`、`cam_top`）可以任意取名，但会成为数据集字段名 —— 请在所有录制过程中保持一致。
> 如果只用一个摄像头，删掉 `cam_top` 那一项即可。

---

## 6. 数据集回放（Replay）

在从手臂上回放已录制的一个 episode，以验证数据：

```bash
lerobot-replay \
  --robot.type=so101_follower \
  --robot.port=/dev/ttyACM1 \
  --robot.id=my_follower \
  --robot.arm_profile=am-follower-6dof \
  --dataset.repo_id=$HF_USER/am_arm_test \
  --dataset.episode=0
```

> AM-ARM200 Pro：将 `am-follower-6dof` 替换为 `am-follower-6dof-hd`。

---

## 7. 数据集可视化

```bash
lerobot-dataset-viz \
  --repo-id $HF_USER/am_arm_test \
  --episode-index 0 \
  --display-compressed-images
```

---

## 8. 训练

### 本地训练

```bash
lerobot-train \
  --dataset.repo_id=$HF_USER/am_arm_test \
  --policy.type=act \
  --output_dir=outputs/train/act_your_dataset1 \
  --job_name=act_your_dataset \
  --policy.device=cuda \
  --wandb.enable=false \
  --policy.repo_id=$HF_USER/act_policy \
  --dataset.video_backend=pyav
```

### 本地没有 GPU？

可以使用任意云 GPU 服务商（例如 AutoDL、Lambda Labs、Vast.ai）。按与本地相同的方式配置环境，运行同样的训练命令，然后把 checkpoint 拷回本机做评估。

---

## 9. 评估

把训练好的模型拷到本机，然后在从手臂上运行推理：

```bash
lerobot-rollout \
  --strategy.type=base \
  --robot.type=so101_follower \
  --robot.port=/dev/ttyACM1 \
  --robot.id=my_follower \
  --robot.arm_profile=am-follower-6dof \
  --policy.path=outputs/train/act_your_dataset1/checkpoints/020000/pretrained_model \
  --task="your task description"
```

> AM-ARM200 Pro：将 `am-follower-6dof` 替换为 `am-follower-6dof-hd`。

---

## 10. 调试

完整的调试工具清单见 [调试命令汇总](../../../examples/debug/README.md)。
