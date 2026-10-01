# tests/yuntao — AlohaMini2 硬件调试脚本

树莓派 5 上做硬件在环调试用的一组脚本。**约定：所有测试/调试脚本都放这个目录**，
不要丢到 `/tmp` 或 `~`。

不依赖 pytest，每个 `.py` 都能直接用 `python xxx.py` 跑；
需要自动化的用 `test_*.py`（也能被 pytest 收集）。

---

## 通用注意事项

### 1. 交互式脚本不能用裸的 `conda run`

```
conda run -n lerobot_alohamini python xxx.py          # ❌ stdin 变管道，读不到按键
conda run --no-capture-output -n lerobot_alohamini python xxx.py   # ✅
conda activate lerobot_alohamini && python xxx.py     # ✅
```

`conda run` 会用管道接管 stdin（`sys.stdin.isatty()` 变 `False`），并缓冲 stdout
（界面不实时刷新）。`lift_ssh.py` / `wheels_ssh.py` 会自动退回读 `/dev/tty` 并告警，
但要看实时界面还是得用上面两种写法。

其余脚本不读键盘，用 `conda run -n lerobot_alohamini python ...` 就行。

### 2. 一律使用 udev 别名，不要写 `/dev/ttyACM*`

`/dev/ttyACM*` 的编号在重启或插拔后会漂移（实测出现过 0/1 → 2/3 → 回到 0/1）。
本机已装 `90-alohamini.rules`（见下）。

### 3. 启动要等 ~5-6 秒

`import lerobot.motors` 会连带导入 torch。所有脚本启动时都会先打一行提示，不是卡死。

### 4. 硬件布局（「option B」，本仓库已改造）

| udev 别名 | 串口序列号 | 波特率 | 挂载的电机 |
| --- | --- | --- | --- |
| `/dev/am_arm_follower_left` | `5B91044456` | 1 000 000 | 物理**左**臂 `arm_left_*` (ID 1-7) + **升降轴** (ID 11) |
| `/dev/am_arm_follower_right` | `5B90148934` | 1 000 000 | 物理**右**臂 `arm_right_*` (ID 1-7) + **底盘三轮** (ID 8,9,10) |

⚠️ 注意升降轴和底盘在**不同**总线上 —— 上游代码假设它们在同一条，所以本仓库做过改造。

手臂关节 ID（profile `am-follower-6dof`）：

| ID | 关节 | 型号 |
| --- | --- | --- |
| 1 | shoulder_pan | sts3095 |
| 2 | shoulder_lift | sts3095 |
| 3 | elbow_flex | sts3095 |
| 4 | wrist_flex | sts3215 |
| 5 | wrist_yaw | sts3215 |
| 6 | wrist_roll | sts3215 |
| 7 | gripper | sts3215 |

型号数字码：`777`=sts3215　`2569`=sts3095　`2825`=sts3250

### 5. 键盘遥控的固有限制

终端只有「按下」事件、没有「松开」事件，所以脚本靠**按键自动重复**来模拟「按住」，
双窗口自适应：`--initial-hold-ms`（默认 700，需大于系统首次重复延迟 250~660ms）
和 `--hold-ms`（默认 200）。代价是**轻点一下会多动最多 700ms**，无法避免。
想更跟手就把系统的键盘重复延迟调短，再相应减小 `--initial-hold-ms`。

---

## 只读诊断类（不改任何电机状态）

这几个可以放心在机器人上电时反复跑。

### `ro.py` — 电机状态只读检查

读取 `Operating_Mode` / `Torque_Enable` / `Lock` / `Acceleration` / `Goal_Velocity`
以及 Position / Velocity / Load / Voltage / Temperature / Current。**全程零写入。**

```bash
python tests/yuntao/ro.py                                     # 检查两条总线
python tests/yuntao/ro.py --port /dev/am_arm_follower_left    # 只查一条
```

> 需要纯只读时用这个，**不要**用 `wheel_test.py --dry-run` —— 后者虽然叫 dry-run，
> 但会写 `Lock` / `Operating_Mode` / `Torque_Enable`。

### `scan_motors.py` — 串口电机扫描

在**所有波特率**下广播 ping 每个端口，报出响应的 ID 和型号。用来排查
「电机没响应」「型号不对」「ID 冲突」。

```bash
python tests/yuntao/scan_motors.py
python tests/yuntao/scan_motors.py --port /dev/am_arm_follower_right
```

### `check_bus_layout.py` — 总线布局校验

按 `model_specs.ROBOT_SPECS` 期望的布局去 `connect(handshake=True)`，
核对每条总线上的电机 ID 和型号是否齐全。改过总线接线后跑这个最快。

```bash
python tests/yuntao/check_bus_layout.py
```

### `identify_arms.py` — 辨认哪条总线接的是哪条臂

提示你用手推动某条臂，脚本报告**哪条总线**动了。做总线相关改动前必跑。

```bash
python tests/yuntao/identify_arms.py
python tests/yuntao/identify_arms.py --hold-s 1.0
```

> 脚本会先检查扭矩状态并提示重力风险。推之前先确认手臂不会砸下来。

---

## 校准类

### `restore_calibration.py` — 把标定写回电机 EEPROM（不重新标定）

等价于标定命令里按回车选「使用现有标定」，但是非交互的，而且**逐项回读校验**。

```bash
python tests/yuntao/restore_calibration.py --dry-run   # 先只读比对，绝对安全
python tests/yuntao/restore_calibration.py            # 真正写入
python tests/yuntao/restore_calibration.py --id 我的机器人 --file /path/to.json
```

标定文件位置：`~/.cache/huggingface/lerobot/calibration/robots/alohamini/<id>.json`
（默认 id 是 `AlohaMiniRobot`）。

要点：
- 写 EEPROM 只需要把 `Lock` 置 0，**不需要关扭矩**，所以手臂全程有支撑、不会掉。
  万一仅解锁写不进，脚本会退回「关扭矩重试」并提示你托住手臂。
- 底盘 (8,9,10) 和升降轴 (11) 的标定恒为 `homing_offset=0, range=0-4095`。

---

## 键盘遥控类（SSH 专用，不需要 X / pynput / VNC）

原理见上文「通用注意事项 5」。

### `lift_ssh.py` — 升降轴遥控

```bash
python tests/yuntao/lift_ssh.py --dry-run          # 只测键盘映射，不碰硬件
python tests/yuntao/lift_ssh.py                    # 实测（默认先归零，再抬到 400mm 停放）
python tests/yuntao/lift_ssh.py --park-mm 300      # 改停放高度
python tests/yuntao/lift_ssh.py --park-mm -1       # 不停放，就留在归零位置
python tests/yuntao/lift_ssh.py --no-home          # 完全不触底
```

按键：`W`/`K`/`U`/`↑` 上升　`S`/`J`/`↓` 下降　`Space` 急停　`H` 重新归零　`Q`/`ESC` 退出

界面：

```
[UP   ] 高度=  +42.31mm  行程=  +42.31mm  电流=  180.5mA  速度= +600  按键=up
```

主要参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--port` | `/dev/am_arm_follower_left` | 升降轴在 left_bus |
| `--robot-model` | `alohamini2` | 决定导程/电机型号，影响高度换算 |
| `--speed` | 600 | 按键时的原始速度（`--v-max` 1300） |
| `--park-mm` | 400 | 归零后抬到该高度停放；`<0` 表示不动 |
| `--current-limit-ma` | 900 | 过流保护阈值 |
| `--max-travel-mm` | 550 | `--no-home` 时的上行行程预算 |
| `--dry-run` | — | 不连硬件，只打印会下发的速度 |

> `--no-home` 时高度只是**相对值**（相对于本次连接起点），且 `descent_floor_mm=5.0`
> 的下限保护会放开，改由行程预算保护。

### `wheels_ssh.py` — 底盘遥控

```bash
python tests/yuntao/wheels_ssh.py --dry-run
python tests/yuntao/wheels_ssh.py
```

按键：`W`/`S` 前进/后退　`A`/`D` 左转/右转　`Z`/`X` 左移/右移　`Q`/`ESC` 退出

参数：`--port`（默认 `/dev/am_arm_follower_right`，底盘在 right_bus）、
`--lin-speed` 0.15 m/s、`--ang-speed` 60 °/s、`--dry-run`。

SSH 掉线是天然安全的：收不到按键字节，按住状态会自动过期，速度归零。
退出时写 0 并**保持扭矩使能**（相当于刹车），避免溜坡。

---

## 非交互驱动类

### `wheel_test.py` — 底盘自动走一遍

不需要键盘，按顺序自动跑几个方向，方便快速验证接线和方向是否正确。
带毫秒级急停（任意键 / `Ctrl+C` / `kill` / `SIGHUP`）。

```bash
python tests/yuntao/wheel_test.py --dry-run        # 只读状态，不驱动
python tests/yuntao/wheel_test.py --yes            # 跳过确认
python tests/yuntao/wheel_test.py --each-s 1.5 --speed 1200
```

> ⚠️ 名字里的 `--dry-run` **不是**零写入，它会写 `Lock` / `Operating_Mode` / `Torque_Enable`。
> 需要纯只读请用 `ro.py`。

---

## 相机类（当前暂停）

> 2026-10 用户为避免机械臂扯断线缆，**拔掉了两台相机**，这部分暂时不用。

### `camera_identify.py` — 给每台相机拍快照用于人工辨认

本机相机型号全是 `Integrated_Webcam_HD` 且**没有可用序列号**（`ID_SERIAL_SHORT` 为空，
`ATTRS{serial}` 返回根 hub 名，同 hub 上全相同），udev 别名只能用 `ENV{ID_PATH}`。
而 `ID_PATH` 本身没有可读含义，所以只能拍照看一眼。

脚本会自动过滤掉树莓派的假 video 节点（ISP 编解码器等）。

```bash
python tests/yuntao/camera_identify.py
python tests/yuntao/camera_identify.py --frames 30 --warmup 3
```

照片存到 `tests/yuntao/camera_snapshots/`（按 `ID_PATH` 命名，已 gitignore），
同时生成 `mapping.txt`。取回本地看：

```bash
scp -r lab408@<Pi_IP>:~/MyProject/lerobot_alohamini/tests/yuntao/camera_snapshots ./
```

### udev 规则文件

| 文件 | 状态 |
| --- | --- |
| `90-alohamini.rules` | **已安装**到 `/etc/udev/rules.d/`，提供两条臂的别名 |
| `91-alohamini-cameras.rules` | **草稿，未安装**。里面的前视/右腕对应是按快照推断的，需人工确认 |

安装/更新：

```bash
sudo cp tests/yuntao/90-alohamini.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/am_*
```

> 每台相机在 `/dev` 下有两个节点（capture + metadata）且 `ID_PATH` **相同**，
> 所以规则里必须加 `ENV{ID_V4L_CAPABILITIES}==":capture:"`（或 `ATTR{index}=="0"`）区分，
> 否则符号链接会随机指向其中一个。

---

## 自动化测试（pty 里模拟按键）

这两个脚本在**伪终端**里跑被测脚本，按可控时序送键，然后断言解析出的速度。
不需要硬件，也不驱动电机（全程 `--dry-run`）。

```bash
python tests/yuntao/test_lift_ssh_keyboard.py
python tests/yuntao/test_wheels_ssh_keyboard.py

# 也可以走 pytest
pytest tests/yuntao/test_lift_ssh_keyboard.py -sv
```

`test_lift_ssh_keyboard.py` 覆盖：

1. 上升/下降的**速度符号**（`dir_sign=-1`，上升下发负速度）与松开归零
2. ↑/↓ 的 ESC 转义序列能被识别
3. `Space` 急停后的**锁存**：必须真正松开方向键才能再次驱动
4. 完全没有终端时明确报错
5. **`conda run` 场景**：stdin 被接管时退回读 `/dev/tty` 并真的能用

`test_wheels_ssh_keyboard.py` 覆盖按键保持检测和双窗口防顿挫。

> 每跑一轮约 40-90 秒（每个用例都要重新 import torch）。

---

## 典型排查流程

**「电机没响应 / 报 Missing motor IDs」**

```bash
python tests/yuntao/scan_motors.py            # 1. 有哪些 ID 活着
python tests/yuntao/check_bus_layout.py       # 2. 布局对不对
python tests/yuntao/identify_arms.py          # 3. 哪条总线是哪条臂
```

**「标定值坏了 / 不想重新标定」**

```bash
python tests/yuntao/restore_calibration.py --dry-run   # 先看差多少
python tests/yuntao/restore_calibration.py             # 再写回去
```

**「想手动动一下底盘 / 升降轴」**

```bash
python tests/yuntao/wheels_ssh.py --dry-run    # 先确认键盘通了
python tests/yuntao/wheels_ssh.py
python tests/yuntao/lift_ssh.py
```

---

## 已知坑

- **升降轴 `home()` 结束时会关掉扭矩**（`Torque_Enable=0`），电机自由。归零后必须
  重新 `enable_torque()`，否则没有支撑、写 `Goal_Velocity` 也不动。
- **升降轴压到底 = 硬堵转**，电流会冲到 ~2000mA，舵机过载保护会让它短暂不响应
  （读位置报 `no status packet`）。所有相关读写都做了容错。`home_down_speed` 已从
  1300 降到 700 减轻这个现象。
- **`AlohaMini.connect()` 每次都会把升降轴压到底归零**。线缆不够长时，
  host 侧可用 `--lift-park-mm 400`（归零后抬升）或 `--no-lift-home`（跳过归零，
  但高度会变成相对值，观测/策略会错位）。
- **`apply_action` 里的 `descent_floor_mm=5.0`** 会在未归零时拦住下降
  （因为起点恰好是 0mm）。
- **alohamini2 的升降电机是 `sts3095`、导程 131 mm/rev**（alohamini1 才是
  `sts3215` + 84 mm/rev）。高度换算要用 `ROBOT_SPECS`，用 `LiftAxisConfig` 的默认值
  会让读数差 1.56 倍。
- **`record_ranges_of_motion()` 要求每个列出的关节都真的动过**，最容易漏掉**夹爪**，
  漏了就会报 `Some motors have the same min and max values`。
  `wrist_roll` 例外（全程旋转，范围强制 0-4095）。
- **`/etc/udev/rules.d/` 里还残留 `99-xlerobot*.rules`**（旧机器人留下的），
  含 `xlerobot_cam_*` 等别名，可能与本项目冲突，XLeRobot 不用的话可以清掉。
