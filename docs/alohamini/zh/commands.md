# AlohaMini 命令速查表

涵盖环境搭建、校准、遥操作、录制、回放、训练、评估和调试的常用命令。

运行前请替换占位符：

- `<Pi_IP>`：树莓派 / 机器人主机端 IP。
- `<your_token>`：具有读写权限的 Hugging Face token。
- `$HF_USER`：Hugging Face 用户名。
- `alohamini1`、`alohamini2`、`alohamini2pro`：必须与主机端实际连接的机器人型号一致。

## 环境

克隆并安装：

```bash
git clone https://github.com/liyiteng/lerobot_alohamini.git
cd lerobot_alohamini
conda create -y -n lerobot_alohamini python=3.12
conda activate lerobot_alohamini
pip install -e ".[all]"
pip install pyzmq feetech-servo-sdk
conda install -y ffmpeg=7.1.1 -c conda-forge
```

使用 uv 的替代方式：

```bash
uv sync --locked
uv sync --locked --extra test --extra dev
uv sync --locked --extra all
```

串口权限：

```bash
sudo usermod -a -G dialout $USER
```

Hugging Face 登录：

```bash
git config --global credential.helper store
hf auth login --token <your_token> --add-to-git-credential
HF_USER=$(hf auth whoami | sed 's/^user=//')
echo $HF_USER
```

## 设备发现

查找电机串口：

```bash
lerobot-find-port
ls /dev/ttyACM*
ls /dev/serial/by-id/
```

查找摄像头：

```bash
lerobot-find-cameras
v4l2-ctl --list-devices
```

查看摄像头支持的格式与帧率：

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext
```

通过编辑摄像头配置来启用或禁用机器人摄像头：

```bash
sudo apt install micro
sudo micro src/lerobot/robots/alohamini/config_alohamini.py
```

在 `alohamini_cameras_config()` 中，取消注释某个摄像头代码块即可启用，注释掉则禁用。
修改摄像头配置后，需要重启 AlohaMini 主机端进程。

## 固定手臂端口

进阶且可选。使用 udev 规则让手臂设备名在重启或 USB 重新插拔后保持稳定。
在插入手臂控制板的机器上运行：

- 从手臂：树莓派 / 机器人主机端。
- 主手臂：PC / DGX 客户端。

查看某块手臂控制板的序列号：

```bash
udevadm info --attribute-walk --name=/dev/ttyACM0 | awk -F'"' '/ATTRS{serial}/{print $2; exit}'
```

更换设备路径，对每块板子重复此操作：

```bash
udevadm info --attribute-walk --name=/dev/ttyACM1 | awk -F'"' '/ATTRS{serial}/{print $2; exit}'
```

创建或编辑 udev 规则文件：

```bash
sudo nano /etc/udev/rules.d/90-mydevice.rules
```

在树莓派 / 机器人主机端添加从手臂规则，使用你板子实际的序列号：

```udev
SUBSYSTEM=="tty", ATTRS{serial}=="<follower_left_serial>", SYMLINK+="am_arm_follower_left"
SUBSYSTEM=="tty", ATTRS{serial}=="<follower_right_serial>", SYMLINK+="am_arm_follower_right"
```

在 PC / DGX 客户端添加主手臂规则，使用你板子实际的序列号：

```udev
SUBSYSTEM=="tty", ATTRS{serial}=="<leader_left_serial>", SYMLINK+="am_arm_leader_left"
SUBSYSTEM=="tty", ATTRS{serial}=="<leader_right_serial>", SYMLINK+="am_arm_leader_right"
```

重新加载并触发 udev：

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

验证这些固定名称：

```bash
ls /dev/am*
```

在树莓派 / 机器人主机端的 `src/lerobot/robots/alohamini/config_alohamini.py` 中使用从手臂路径：

```python
left_port = "/dev/am_arm_follower_left"
right_port = "/dev/am_arm_follower_right"
```

在 PC / DGX 客户端的 `examples/alohamini/record_bi.py` 和 `examples/alohamini/teleoperate_bi.py` 中使用主手臂路径：

```python
left_arm_config=SOLeaderConfig(port="/dev/am_arm_leader_left", ...)
right_arm_config=SOLeaderConfig(port="/dev/am_arm_leader_right", ...)
```

## 主机端

以下命令在树莓派 / 机器人主机端运行。

只做校准，然后退出：

```bash
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini2
```

下面的主机端命令也会检查校准，缺少时会自动提示校准。

AlohaMini 1：

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini1
```

AlohaMini 2：

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2
```

AlohaMini 2 Pro：

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2pro
```

仅底盘与升降轴：

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2 --no_follower
```

### 升降轴：停靠高度

每次连接时，主机端都会把升降轴向下驱动到硬限位以重新回零
（`0 mm` 就在这里定义）。如果手臂线缆太短、无法完全降到最低位，
可以在回零后立即抬起升降轴：

```bash
python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini2 \
  --lift-park-mm 400
```

若要把这作为该机器的永久默认值，请改为在
`src/lerobot/robots/alohamini/config_alohamini.py` 中设置：

```python
lift_park_height_mm: float | None = 400.0
```

如果线缆完全无法承受短暂触底，可以彻底跳过回零：

```bash
python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini2 \
  --no-lift-home
```

> ⚠️ `--no-lift-home` 会让 `lift_axis.height_mm` 相对于上一次回零的零点，
> 因此录制到的观测数据以及使用升降高度的策略会出现前后不一致。
> 只有在硬件条件实在不允许时才使用它。
> 它不能与 `--lift-park-mm` 同时使用（停靠需要一个绝对零点）。

兼容 ROS 的摄像头发布是可选项，不会改变默认的双摄像头集合。
本分支以 50 Hz 运行主机端控制：

```bash
python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini2pro \
  --camera-stream
```

主机端使用 TCP 5555 传输指令，5556 传输观测与元数据，
5557 传输可选的纯摄像头流。普通的 5556 请求仍保留旧式的
「状态 + 图像」响应；以 `:state` 结尾的请求令牌只返回状态。
原生遥操作默认 50 Hz 控制、30 Hz 摄像头请求。这些可以通过
`--fps 50 --camera-fps 30` 显式设置。现有的
`record_bi.py` 保持它原来的单速率行为：控制和
数据集采样都使用 `--dataset.fps`。如果你想在 50 Hz 控制的同时
只按数据集速率（通常是 30 Hz）写入新鲜的、时间戳对齐的图像、
状态和动作，请使用 `record_bi_multirate.py`。多速率录制器
会采集到请求的帧数，因此一个实际运行在 29.5 Hz 的摄像头可能会
稍微延长实际录制时长。如果摄像头停滞、
多摄像头时间偏差超过 50 ms、状态对齐超过 100 ms，或
新鲜帧率低于请求帧率的 90%，它会显式停止。

## 遥操作

主机端运行后，在 PC / DGX 客户端运行以下命令。

AlohaMini 1 搭配 SO-ARM 主手臂：

```bash
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof \
  --fps 50 \
  --camera-fps 30
```

AlohaMini 2 / 2 Pro 搭配 AM-ARM 主手臂：

```bash
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --fps 50 \
  --camera-fps 30
```

排查网络或 CPU 问题时可以降低帧率：

```bash
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --fps 10 \
  --camera-fps 10
```

## 录制

`record_bi.py` 在初始化和收尾后会打印本地数据集路径，并默认上传到 Hugging Face Hub。
如果只想把数据集保留在本地，请加上 `--dataset.push_to_hub=false`。
如果想存到指定的本地目录或从中续录，请加上 `--dataset.root /path/to/dataset`。

若要在 `--dataset.fps` 的新鲜摄像头帧下启用可选的 50 Hz 控制，
请用相同的参数改用多速率入口：

```bash
python examples/alohamini/record_bi_multirate.py \
  --dataset.repo_id $HF_USER/alohamini_multirate_test \
  --dataset.fps 30 \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2pro \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

为 AlohaMini 1 新建数据集：

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/so100_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof
```

为 AlohaMini 1 续录数据集：

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/so100_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof \
  --resume
```

为 AlohaMini 2 / 2 Pro 新建数据集：

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

为 AlohaMini 2 / 2 Pro 续录数据集：

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --resume
```

录制冒烟测试：

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/alohamini_smoke_test \
  --dataset.num_episodes 1 \
  --dataset.fps 10 \
  --dataset.episode_time_s 10 \
  --dataset.reset_time_s 3 \
  --dataset.single_task "smoke test" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

## 回放与可视化

回放一个 episode：

```bash
python examples/alohamini/replay_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.episode 0 \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2
```

如果数据集不在 `$HF_LEROBOT_HOME/$HF_USER/am2_bi_test` 下，请加上 `--dataset.root /path/to/am2_bi_test`。

可视化一个数据集 episode：

```bash
lerobot-dataset-viz \
  --repo-id $HF_USER/am2_bi_test \
  --episode-index 0 \
  --display-compressed-images
```

## 训练

训练 ACT：

```bash
lerobot-train \
  --dataset.repo_id=$HF_USER/am2_bi_test \
  --policy.type=act \
  --output_dir=outputs/train/act_am2_bi_test \
  --job_name=act_am2_bi_test \
  --policy.device=cuda \
  --wandb.enable=false \
  --policy.repo_id=$HF_USER/act_am2_bi_test \
  --dataset.video_backend=pyav
```

使用 uv 训练：

```bash
uv run lerobot-train \
  --dataset.repo_id=$HF_USER/am2_bi_test \
  --policy.type=act \
  --output_dir=outputs/train/act_am2_bi_test \
  --job_name=act_am2_bi_test \
  --policy.device=cuda \
  --wandb.enable=false \
  --policy.repo_id=$HF_USER/act_am2_bi_test \
  --dataset.video_backend=pyav
```

## 评估

评估本地 checkpoint：

```bash
python examples/alohamini/evaluate_bi.py \
  --eval.n_episodes 3 \
  --fps 20 \
  --eval.episode_time_s 45 \
  --dataset.single_task "Pick and place task" \
  --policy.path outputs/train/act_am2_bi_test/checkpoints/020000/pretrained_model \
  --dataset.repo_id $HF_USER/eval_act_am2_bi_test \
  --dataset.push_to_hub=true \
  --robot.remote_ip <Pi_IP> \
  --robot.id my_alohamini \
  --robot.robot_model alohamini2
```

## 性能调试

检查网络延迟：

```bash
ping <Pi_IP>
```

检查 WiFi 链路：

```bash
iw dev
iw dev wlan0 link
```

用 iperf3 检查带宽：

```bash
# 主机端
iperf3 -s

# 客户端
iperf3 -c <Pi_IP>
```

监控 CPU、内存和 GPU：

```bash
top
htop
nvidia-smi
```

查看 FFmpeg 可用的视频编码器：

```bash
ffmpeg -hide_banner -encoders | grep -E 'libsvtav1|h264|nvenc|vaapi|qsv'
```

查看 Python 包版本：

```bash
python -c "import av, cv2, torch; print('av', av.__version__); print('cv2', cv2.__version__); print('torch', torch.__version__)"
```

运行一次低负载录制测试：

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/perf_debug_low_fps \
  --dataset.num_episodes 1 \
  --dataset.fps 10 \
  --dataset.episode_time_s 10 \
  --dataset.reset_time_s 3 \
  --dataset.single_task "perf debug" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

## 硬件调试脚本

这些命令来自 [examples/debug](../../../examples/debug/)。请在仓库根目录下运行。

**该用哪个端口。** `/dev/ttyACM*` 的编号在重启和 USB 重新插拔之间会漂移，
因此优先使用固定的 udev 别名（见上方[固定手臂端口](#固定手臂端口)）。
挂在每个别名上的电机如下：

| 别名 | 电机 |
| --- | --- |
| `/dev/am_arm_follower_left`  | 左臂 `arm_left_*`（ID 1–7）+ 升降轴（ID 11） |
| `/dev/am_arm_follower_right` | 右臂 `arm_right_*`（ID 1–7）+ 移动底盘（ID 8、9、10） |

注意升降轴和移动底盘位于**不同**的总线上。

查看所有电机状态：

```bash
python examples/debug/motors.py get_motors_states \
  --port /dev/am_arm_follower_left
```

仅控制移动底盘（底盘在右侧总线）：

```bash
python examples/debug/wheels.py \
  --port /dev/am_arm_follower_right
```

仅控制升降轴（升降轴在左侧总线）：

```bash
python examples/debug/axis.py \
  --port /dev/am_arm_follower_left
```

按 ID 旋转指定电机：

```bash
python examples/debug/motors.py move_motor_to_position \
  --id 1 \
  --position 2 \
  --port /dev/ttyACM0
```

设置新的电机 ID：

```bash
python examples/debug/motors.py configure_motor_id \
  --id 1 \
  --set_id 8 \
  --port /dev/ttyACM0
```

设置指定舵机的相位：

```bash
python examples/debug/motors.py configure_motor_phase \
  --id 1 \
  --set_phase 12 \
  --port /dev/ttyACM0
```

设置所有舵机的相位：

```bash
python examples/debug/motors.py configure_motor_phase \
  --set_phase 12 \
  --port /dev/ttyACM0
```

把当前位置重置为电机中位：

```bash
python examples/debug/motors.py reset_motors_to_midpoint \
  --port /dev/ttyACM1
```

关闭所有手臂电机的力矩：

```bash
python examples/debug/motors.py reset_motors_torque \
  --port /dev/ttyACM0
```

在机器人手臂上执行动作脚本：

```bash
python examples/debug/motors.py move_motors_by_script \
  --script_path examples/debug/action_scripts/test_dance.txt \
  --port /dev/ttyACM0
```

用脚本让手臂回到休息位：

```bash
python examples/debug/motors.py move_motors_by_script \
  --script_path examples/debug/action_scripts/go_to_restposition.txt \
  --port /dev/ttyACM0
```

用脚本让手臂回到中位：

```bash
python examples/debug/motors.py move_motors_by_script \
  --script_path examples/debug/action_scripts/go_to_midpoint.txt \
  --port /dev/ttyACM0
```

运行摄像头调试脚本：

```bash
python examples/debug/test_cv.py
```

运行 CUDA 调试脚本：

```bash
python examples/debug/test_cuda.py
```

运行网络调试脚本：

```bash
python examples/debug/test_network.py
```

运行数据集调试脚本：

```bash
python examples/debug/test_dataset.py
```

运行键盘 / 输入调试脚本：

```bash
python examples/debug/test_input.py
```

运行麦克风调试脚本：

```bash
python examples/debug/test_mic.py
```

## 开发

运行测试：

```bash
uv run pytest tests -svv --maxfail=10
```

运行 pre-commit 检查：

```bash
pre-commit run --all-files
```

运行端到端测试：

```bash
DEVICE=cuda make test-end-to-end
```

查找 AlohaMini 相关引用：

```bash
rg -n "alohamini|record_bi|teleoperate_bi|evaluate_bi" src examples docs
```
