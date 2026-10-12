# AlohaMini Command Cheat Sheet

Common commands for setup, calibration, teleoperation, recording, replay, training, evaluation, and debugging.

Replace placeholders before running:

- `<Pi_IP>`: Raspberry Pi / robot host IP.
- `<your_token>`: Hugging Face token with read and write permissions.
- `$HF_USER`: Hugging Face username.
- `alohamini1`, `alohamini2`, `alohamini2pro`: must match the physical robot on the host side.

## Environment

Clone and install:

```bash
git clone https://github.com/liyiteng/lerobot_alohamini.git
cd lerobot_alohamini
conda create -y -n lerobot_alohamini python=3.12
conda activate lerobot_alohamini
pip install -e ".[all]"
pip install pyzmq feetech-servo-sdk
conda install -y ffmpeg=7.1.1 -c conda-forge
```

Alternative with uv:

```bash
uv sync --locked
uv sync --locked --extra test --extra dev
uv sync --locked --extra all
```

Serial permissions:

```bash
sudo usermod -a -G dialout $USER
```

Hugging Face login:

```bash
git config --global credential.helper store
hf auth login --token <your_token> --add-to-git-credential
HF_USER=$(hf auth whoami | sed 's/^user=//')
echo $HF_USER
```

## Device Discovery

Find motor serial ports:

```bash
lerobot-find-port
ls /dev/ttyACM*
ls /dev/serial/by-id/
```

Find cameras:

```bash
lerobot-find-cameras
v4l2-ctl --list-devices
```

Check camera formats and FPS:

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext
```

Enable or disable robot cameras by editing the camera config:

```bash
sudo apt install micro
sudo micro src/lerobot/robots/alohamini/config_alohamini.py
```

In `alohamini_cameras_config()`, uncomment a camera block to enable it, or comment it out to disable it.
After changing camera config, restart the AlohaMini host process.

## Persistent Arm Ports

Advanced and optional. Use udev rules to keep arm device names stable after reboot or USB reconnect.
Run this on the machine where the arm controller boards are plugged in:

- Follower arms: Raspberry Pi / robot host.
- Leader arms: PC / DGX client.

Find the serial number of one arm controller board:

```bash
udevadm info --attribute-walk --name=/dev/ttyACM0 | awk -F'"' '/ATTRS{serial}/{print $2; exit}'
```

Repeat for each board by changing the device path:

```bash
udevadm info --attribute-walk --name=/dev/ttyACM1 | awk -F'"' '/ATTRS{serial}/{print $2; exit}'
```

Create or edit the udev rules file:

```bash
sudo nano /etc/udev/rules.d/90-mydevice.rules
```

Add follower-arm rules on the Pi / robot host, using the actual serial numbers from your boards:

```udev
SUBSYSTEM=="tty", ATTRS{serial}=="<follower_left_serial>", SYMLINK+="am_arm_follower_left"
SUBSYSTEM=="tty", ATTRS{serial}=="<follower_right_serial>", SYMLINK+="am_arm_follower_right"
```

Add leader-arm rules on the PC / DGX client, using the actual serial numbers from your boards:

```udev
SUBSYSTEM=="tty", ATTRS{serial}=="<leader_left_serial>", SYMLINK+="am_arm_leader_left"
SUBSYSTEM=="tty", ATTRS{serial}=="<leader_right_serial>", SYMLINK+="am_arm_leader_right"
```

Reload and trigger udev:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Verify the stable names:

```bash
ls /dev/am*
```

Use follower paths in `src/lerobot/robots/alohamini/config_alohamini.py` on the Pi / robot host:

```python
left_port = "/dev/am_arm_follower_left"
right_port = "/dev/am_arm_follower_right"
```

Use leader paths in `examples/alohamini/record_bi.py` and `examples/alohamini/teleoperate_bi.py` on the PC / DGX client:

```python
left_arm_config=SOLeaderConfig(port="/dev/am_arm_leader_left", ...)
right_arm_config=SOLeaderConfig(port="/dev/am_arm_leader_right", ...)
```

## Host Side

Run these on the Raspberry Pi / robot host.

Calibrate only, then exit:

```bash
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini2
```

The host commands below also check calibration and will prompt calibration automatically if it is missing.

AlohaMini 1:

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini1
```

AlohaMini 2:

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2
```

AlohaMini 2 Pro:

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2pro
```

Base and lift only:

```bash
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2 --no_follower
```

### Single-arm (right arm) mode: `--parts`

`--parts` enables only a subset of the whole robot. Values are a comma-separated subset of
`left_arm` / `right_arm` / `base` / `lift`; the default is `all` (the full robot).

**Recommended combination for single-arm recording** — the Host only connects the right arm
and never touches the left arm, the lift axis, or the base:

```bash
python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini2 \
  --parts right_arm \
  --cameras forward,wrist_right
```

- `--parts right_arm`: the left bus is not created at all (no left arm, no lift), and the right
  bus is configured with the 7 right-arm motors only (base IDs 8/9/10 are never addressed).
  As a result **the lift axis does not home downward on `connect()`** and the base never
  receives a velocity command.
- `--cameras` is optional and drops cameras you do not need. In a right-arm task
  `wrist_left` stares at the unused left arm, so recording it only wastes disk and training VRAM.

> ⚠️ `--parts` must match the recorder's `--robot.parts`, and `--cameras` must match
> `--robot.cameras`. The client validates the Host's `_robot_metadata["enabled_parts"]` during
> the handshake and fails immediately when a part it needs is missing, instead of discovering
> it halfway through a recording. The reverse is allowed (full-robot Host, right-arm client):
> the left arm / lift / base stay connected but are never commanded.

Calibrate only the enabled parts (other motors are not swung):

```bash
python -m lerobot.robots.alohamini.alohamini_calibrate \
  --robot_model alohamini2 \
  --parts right_arm
```

### Lift axis: park height

On every connect the Host drives the lift axis down to its hard stop to re-home it
(that is where `0 mm` is defined). If the arm cabling is too short to sit fully
lowered, raise the lift right after homing:

```bash
python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini2 \
  --lift-park-mm 400
```

To make that the permanent default for this machine, set it in
`src/lerobot/robots/alohamini/config_alohamini.py` instead:

```python
lift_park_height_mm: float | None = 400.0
```

If the cabling cannot take the brief bottom-out at all, skip homing entirely:

```bash
python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini2 \
  --no-lift-home
```

> ⚠️ `--no-lift-home` leaves `lift_axis.height_mm` relative to the previous homing,
> so recorded observations and policies that use the lift height will be
> inconsistent. Only use it when the hardware leaves you no choice.
> It cannot be combined with `--lift-park-mm` (parking needs an absolute zero).

ROS-compatible camera publishing is opt-in and does not change the default
camera set (`forward`, `wrist_left`, `wrist_right`). This branch runs Host control at 50 Hz:

```bash
python -m lerobot.robots.alohamini.alohamini_host \
  --robot_model alohamini2pro \
  --camera-stream
```

The Host uses TCP 5555 for commands, 5556 for observations and metadata, and
5557 for the optional camera-only stream. Plain 5556 requests retain the legacy
state-plus-image response; request tokens ending in `:state` return state only.
Native teleoperation defaults to 50 Hz control and 30 Hz camera requests. These
can be set explicitly with `--fps 50 --camera-fps 30`. The existing
`record_bi.py` keeps its original single-rate behavior, where control and
dataset sampling both use `--dataset.fps`. Use `record_bi_multirate.py` when
you want 50 Hz control while committing only fresh, timestamp-aligned images,
state, and action at the dataset rate (normally 30 Hz). The multirate recorder
collects the requested frame count, so a physical camera running at 29.5 Hz may
extend wall-clock recording slightly. It stops explicitly if a camera stalls,
multi-camera skew exceeds 50 ms, state alignment exceeds 100 ms, or the fresh
frame rate falls below 90% of the requested rate.

## Teleoperation

Run these on the PC / DGX client after the host is running.

AlohaMini 1 with SO-ARM leader:

```bash
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof \
  --fps 50 \
  --camera-fps 30
```

AlohaMini 2 / 2 Pro with AM-ARM leader:

```bash
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --fps 50 \
  --camera-fps 30
```

Lower FPS for network or CPU debugging:

```bash
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --fps 10 \
  --camera-fps 10
```

Single-arm (right arm) teleoperation: start the Host with `--parts right_arm` and pass
`--teleop.arm right` (connect only the right leader) plus `--robot.parts right_arm`
(never command the left arm / base / lift):

```bash
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --robot.parts right_arm \
  --teleop.arm right \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --fps 50 \
  --camera-fps 30
```

## Recording

`record_bi.py` prints the local dataset path after setup and finalization, and uploads to Hugging Face Hub by default.
Add `--dataset.push_to_hub=false` if you only want to keep the dataset locally.
Add `--dataset.root /path/to/dataset` when you want to store or resume from a specific local directory.

For optional 50 Hz control with fresh camera frames at `--dataset.fps`, use the
same arguments with the multirate entry point:

```bash
python examples/alohamini/record_bi_multirate.py \
  --dataset.repo_id $HF_USER/alohamini_multirate_test \
  --dataset.fps 30 \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2pro \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

Create a dataset for AlohaMini 1:

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

Resume a dataset for AlohaMini 1:

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

Create a dataset for AlohaMini 2 / 2 Pro:

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

Resume a dataset for AlohaMini 2 / 2 Pro:

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

Recording smoke test:

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

### Single-arm (right arm) dataset

With the Host started using `--parts right_arm`, add three flags on the recorder side:

- `--teleop.arm right`: connect and read **only** the right leader
  (`/dev/am_arm_leader_right`). Its keys are prefixed with `right_`, which lines up with the
  follower's `arm_right_*.pos`. The calibration file is still `<teleop.id>_right`, i.e. the
  same one the bimanual right arm uses, so no re-calibration is needed.
- `--robot.parts right_arm`: keep only the 7 right-arm dimensions in the dataset
  (`observation.state` / `action` are both 7-D) — no left arm, base, or lift.
- `--robot.cameras forward,wrist_right`: must match the Host's `--cameras`.

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/am2_right_arm_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --robot.parts right_arm \
  --robot.cameras forward,wrist_right \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --teleop.arm right \
  --dataset.push_to_hub=false
```

Add `--resume` to the same command to append episodes. At startup the script prints a
one-line self-check — confirm it matches your intent:

```
Recording mode: parts=['right_arm'] leader=right cameras=['forward', 'wrist_right'] action_dims=7
```

`action_dims` must be 7 (18 for the full robot). If `--teleop.arm` and `--robot.parts` disagree
(e.g. the leader sends left-arm keys while the robot only exposes the right arm), the script
fails **before connecting any device**, because such an action payload would be rejected whole
by `send_action()`.

> ⚠️ A single-arm run **cannot** `--resume` into a full-robot dataset (or vice versa):
> `LeRobotDataset.resume` builds frames from the old dataset's `features`, so the dimension and
> name mismatch raises `KeyError` on the first frame. Start a new dataset when switching modes.

## Replay and Visualization

Replay one episode:

```bash
python examples/alohamini/replay_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.episode 0 \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2
```

If the dataset is not under `$HF_LEROBOT_HOME/$HF_USER/am2_bi_test`, add `--dataset.root /path/to/am2_bi_test`.

Visualize a dataset episode:

```bash
lerobot-dataset-viz \
  --repo-id $HF_USER/am2_bi_test \
  --episode-index 0 \
  --display-compressed-images
```

## Training

Train ACT:

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

Train with uv:

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

### π₀.₅ finetuning

π₀.₅ needs `pip install -e ".[pi]"` and loads pretrained weights from `lerobot/pi05_base`.
A single-arm dataset has only 7-D `state`/`action`; π₀.₅ zero-pads them to
`max_state_dim` / `max_action_dim` (32 by default), so no extra configuration is required.

```bash
lerobot-train \
  --dataset.repo_id=$HF_USER/am2_right_arm_test \
  --policy.type=pi05 \
  --policy.pretrained_path=lerobot/pi05_base \
  --policy.device=cuda \
  --policy.dtype=bfloat16 \
  --policy.gradient_checkpointing=true \
  --output_dir=outputs/train/pi05_am2_right_arm \
  --job_name=pi05_am2_right_arm \
  --policy.repo_id=$HF_USER/pi05_am2_right_arm \
  --batch_size=8 \
  --steps=30000 \
  --policy.scheduler_decay_steps=30000 \
  --policy.scheduler_warmup_steps=1000 \
  --save_freq=5000 \
  --wandb.enable=false \
  --dataset.video_backend=pyav
```

- Size `--steps` as 5–10 epochs: `total_frames / batch_size * epochs`
  (see `AGENT_GUIDE.md` §7.2 in the repository root).
- π₀.₅ peaks at roughly **16 GB** at batch=1 (more with the default AdamW). When memory is
  tight, add `--policy.train_expert_only=true` (freeze the VLM, train the action expert) and
  lower `--batch_size`.
- π₀.₅ normalizes state/action with **quantiles** (q01/q99). Datasets recorded with this
  repository already contain quantile stats; for an older dataset, add them first or training
  fails outright:

  ```bash
  python src/lerobot/scripts/augment_dataset_quantile_stats.py --repo-id=$HF_USER/am2_right_arm_test
  ```

  Alternatively switch to mean/std normalization:
  `--policy.normalization_mapping='{"ACTION": "MEAN_STD", "STATE": "MEAN_STD", "VISUAL": "IDENTITY"}'`.

## Evaluation

Evaluate a local checkpoint:

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

Evaluating a single-arm (right arm) policy: add `--robot.parts right_arm --robot.cameras
forward,wrist_right`, matching the Host's `--parts` / `--cameras` and the dataset the policy was
trained on:

```bash
python examples/alohamini/evaluate_bi.py \
  --eval.n_episodes 3 \
  --fps 20 \
  --eval.episode_time_s 45 \
  --dataset.single_task "pickup1" \
  --policy.path outputs/train/pi05_am2_right_arm/checkpoints/030000/pretrained_model \
  --dataset.repo_id $HF_USER/eval_pi05_am2_right_arm \
  --dataset.push_to_hub=false \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --robot.parts right_arm \
  --robot.cameras forward,wrist_right
```

## Performance Debugging

Check network latency:

```bash
ping <Pi_IP>
```

Check WiFi link:

```bash
iw dev
iw dev wlan0 link
```

Check bandwidth with iperf3:

```bash
# Host side
iperf3 -s

# Client side
iperf3 -c <Pi_IP>
```

Monitor CPU, memory, and GPU:

```bash
top
htop
nvidia-smi
```

Check video encoders available to FFmpeg:

```bash
ffmpeg -hide_banner -encoders | grep -E 'libsvtav1|h264|nvenc|vaapi|qsv'
```

Check Python package versions:

```bash
python -c "import av, cv2, torch; print('av', av.__version__); print('cv2', cv2.__version__); print('torch', torch.__version__)"
```

Run a lower-load recording test:

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

## Hardware Debug Scripts

These commands come from [examples/debug](../../examples/debug/). Run them from the repository root.

**Which port to use.** `/dev/ttyACM*` numbering drifts between reboots and USB reconnects,
so prefer the stable udev aliases (see [Persistent Arm Ports](#persistent-arm-ports) above).
The motors that live on each alias are:

| Alias | Motors |
| --- | --- |
| `/dev/am_arm_follower_left`  | left arm `arm_left_*` (IDs 1–7) + lift axis (ID 11) |
| `/dev/am_arm_follower_right` | right arm `arm_right_*` (IDs 1–7) + mobile base (IDs 8, 9, 10) |

Note that the lift axis and the mobile base are on **different** buses.

View all motor states:

```bash
python examples/debug/motors.py get_motors_states \
  --port /dev/am_arm_follower_left
```

Control the mobile base only (base is on the right bus):

```bash
python examples/debug/wheels.py \
  --port /dev/am_arm_follower_right
```

Control the lift axis only (lift is on the left bus):

```bash
python examples/debug/axis.py \
  --port /dev/am_arm_follower_left
```

Rotate a specific motor by ID:

```bash
python examples/debug/motors.py move_motor_to_position \
  --id 1 \
  --position 2 \
  --port /dev/ttyACM0
```

Set a new motor ID:

```bash
python examples/debug/motors.py configure_motor_id \
  --id 1 \
  --set_id 8 \
  --port /dev/ttyACM0
```

Set the phase of a specified servo:

```bash
python examples/debug/motors.py configure_motor_phase \
  --id 1 \
  --set_phase 12 \
  --port /dev/ttyACM0
```

Set the phase for all servos:

```bash
python examples/debug/motors.py configure_motor_phase \
  --set_phase 12 \
  --port /dev/ttyACM0
```

Reset current position as the motor midpoint:

```bash
python examples/debug/motors.py reset_motors_to_midpoint \
  --port /dev/ttyACM1
```

Disable torque for all arm motors:

```bash
python examples/debug/motors.py reset_motors_torque \
  --port /dev/ttyACM0
```

Execute an action script on the robot arm:

```bash
python examples/debug/motors.py move_motors_by_script \
  --script_path examples/debug/action_scripts/test_dance.txt \
  --port /dev/ttyACM0
```

Move arm to rest position from script:

```bash
python examples/debug/motors.py move_motors_by_script \
  --script_path examples/debug/action_scripts/go_to_restposition.txt \
  --port /dev/ttyACM0
```

Move arm to midpoint from script:

```bash
python examples/debug/motors.py move_motors_by_script \
  --script_path examples/debug/action_scripts/go_to_midpoint.txt \
  --port /dev/ttyACM0
```

Run camera debug script:

```bash
python examples/debug/test_cv.py
```

Run CUDA debug script:

```bash
python examples/debug/test_cuda.py
```

Run network debug script:

```bash
python examples/debug/test_network.py
```

Run dataset debug script:

```bash
python examples/debug/test_dataset.py
```

Run keyboard/input debug script:

```bash
python examples/debug/test_input.py
```

Run microphone debug script:

```bash
python examples/debug/test_mic.py
```

## Development

Run tests:

```bash
uv run pytest tests -svv --maxfail=10
```

Run pre-commit checks:

```bash
pre-commit run --all-files
```

Run end-to-end tests:

```bash
DEVICE=cuda make test-end-to-end
```

Find AlohaMini references:

```bash
rg -n "alohamini|record_bi|teleoperate_bi|evaluate_bi" src examples docs
```
