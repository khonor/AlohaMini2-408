# tests/yuntao —— AlohaMini2 键盘遥控脚本（升降轴 / 底盘）

树莓派 5 上做硬件在环调试用的脚本。**约定：所有测试/调试脚本都放这个目录**，
不要丢到 `/tmp` 或 `~`。

同类型的脚本只保留一份（键盘遥控版），所以这个目录里**只有两个脚本**：

```
tests/yuntao/
├── test.md                      ← 本文档
├── 01_lift_ssh.py               ← 脚本 1：升降轴（中轴）键盘遥控
├── 02_wheels_ssh.py             ← 脚本 2：底盘三轮键盘遥控
├── 90-alohamini.rules           ← udev 规则【装到**树莓派**】：从臂 + 升降轴 + 底盘
├── 90-alohamini-leader.rules    ← udev 规则【装到 **PC / 操作端**】：两条主臂（仓库备份）
├── 91-alohamini-cameras.rules   ← udev 规则【装到**树莓派**】：三路 USB 相机 -> /dev/am_camera_*
└── camera_snapshots/            ← 相机画面快照（按 USB 口命名，用于确认哪路是哪路）
```

---

## 一、脚本清单与运行顺序

| 顺序 | 脚本 | 一句话功能 | 对硬件的写入 | 默认端口 |
| --- | --- | --- | --- | --- |
| 1 | `01_lift_ssh.py` | **升降轴（中轴）**键盘遥控 | 驱动（速度模式） | `/dev/am_arm_follower_left` |
| 2 | `02_wheels_ssh.py` | **底盘三轮**键盘遥控 | 驱动（速度模式） | `/dev/am_arm_follower_right` |

**为什么先升降后底盘**：升降轴是垂直运动，不会让机器人位移，出问题也只在原地；
底盘一开就会跑，需要更大的地面空间。先把不动的测通，再测会跑的。

> 两个脚本都支持 `--dry-run`：只测键盘映射，**不连接、不驱动电机**。
> 第一次用、或者换了终端环境，先跑 `--dry-run` 确认按键能收到。

---

## 二、跑之前必看

### 2.1 串口一律用 udev 别名，不要写 `/dev/ttyACM*`

`/dev/ttyACM*` 的编号在重启或插拔后会漂移（实测出现过 `0/1 → 2/3 → 回到 0/1`）。
本机已安装 `90-alohamini.rules`，提供两条固定别名：

| udev 别名 | 串口序列号 | 波特率 | 挂载的电机 |
| --- | --- | --- | --- |
| `/dev/am_arm_follower_left` | `5B91044456` | 1 000 000 | 物理**左**臂 (ID 1-7) + **升降轴** (ID 11, sts3095) |
| `/dev/am_arm_follower_right` | `5B90148934` | 1 000 000 | 物理**右**臂 (ID 1-7) + **底盘三轮** (ID 8,9,10, sts3215) |

> ⚠️ 上游设计假设「升降轴和底盘在同一条总线」，**本仓库已改造**：底盘挂右总线、
> 升降留左总线。上表是**本机实机**布局（"option B"）。
>
> 所以：`01_lift_ssh.py` 默认端口是 **left**，`02_wheels_ssh.py` 默认端口是 **right**。

安装 / 更新规则：

```bash
sudo cp tests/yuntao/90-alohamini.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --action=add --subsystem-match=tty
ls -l /dev/am_arm_follower_*
```

查控制板序列号（换板子之后用）：

```bash
udevadm info --attribute-walk --name=/dev/ttyACM0 | awk -F'"' '/ATTRS{serial}/{print $2; exit}'
```

> 📌 **仓库里有两份规则，别搞混**：
>
> | 文件 | 装在哪 | 产出别名 | 序列号 |
> | --- | --- | --- | --- |
> | `90-alohamini.rules` | **树莓派**（从臂 + 升降轴 + 底盘） | `/dev/am_arm_follower_left\|right` | `5B91044456` / `5B90148934` |
> | `90-alohamini-leader.rules` | **PC / 操作端**（两条主臂） | `/dev/am_arm_leader_left\|right` | `5B91030671` / `5B79016183` |
>
> 本文档的两个脚本只关心 **follower** 那份；**leader** 那份是给
> `examples/alohamini/teleoperate_bi.py` 用的，装在这台 PC 上。
> 两份的属组也不同（树莓派用 `dialout`，PC 用 `plugdev`，原因见 leader 那份的注释）。

### 2.2 交互式脚本不能用裸的 `conda run`

```
conda run -n lerobot_alohamini python xxx.py                       # ❌ stdin 变管道，读不到按键
conda run --no-capture-output -n lerobot_alohamini python xxx.py   # ✅
conda activate lerobot_alohamini && python xxx.py                  # ✅
```

`conda run` 会用管道接管 stdin（`sys.stdin.isatty()` 变 `False`），并缓冲 stdout
（界面不实时刷新）。这两个脚本在检测到 stdin 不是终端时，会**自动退回读 `/dev/tty`**
并打印告警，但要看实时界面还是得用上面两种写法。

### 2.3 启动要等 ~5-6 秒

`import lerobot.motors` 会连带导入 torch。脚本启动时都会先打一行提示，不是卡死。

### 2.4 键盘遥控的固有限制

终端只有「按下」事件、没有「松开」事件，所以脚本靠**按键自动重复**来模拟「按住」，
用双窗口自适应：

- `--initial-hold-ms`（默认 700）：刚按下时用，需大于系统首次重复延迟（250~660 ms）
- `--hold-ms`（默认 200）：重复已连续后用，松开后能迅速停下

代价是**轻点一下会多动最多 700 ms**，无法避免。想更跟手就把系统的键盘重复延迟调短
（X11: `xset r rate 200 40`；Windows: 设置→键盘），再相应减小 `--initial-hold-ms`。

### 2.5 为什么不用 `pynput` / `examples/debug/*`

`examples/debug/wheels.py`、`examples/debug/axis.py` 用 `pynput` 抓全局键盘，
只能走 X11：SSH 会话里 `DISPLAY` 为空会直接导入报错；即使在树莓派本机显示器上运行，
Wayland 原生终端也不会把按键交给 XWayland，结果是脚本正常运行却永远收不到按键。
这两个脚本改用 lerobot 自带的 `TerminalKeyListener`（cbreak 模式直接读控制终端），
 **不需要 X / pynput / VNC**，SSH 里就能用。

### 2.6 为什么规则文件用 `90-` 开头

udev 的加载规则（`man 7 udev`）：

- 所有目录（`/etc/udev/rules.d/`、`/run/udev/rules.d/`、`/usr/lib/udev/rules.d/`）下的规则文件
  会被**汇总后按文件名字典序统一排序处理**，所以**数字前缀就是执行顺序**。
- 同名文件会互相覆盖：`/etc/` 优先级最高，`/run/` 高于 `/usr/lib/`。
  所以 `/etc/udev/rules.d/50-udev-default.rules` 能整体替掉发行版那份。

于是编号有两层含义：

| 号段 | 用途 |
| --- | --- |
| `50`–`89` | 发行版 / 软件包自带（本机实测：`50-udev-default.rules`、`60-*`、`70-*`、`77-*`、`80-*` …） |
| `90`–`99` | 约定留给**本地管理员**的自定义规则 |

把自定义规则放 `90` 段有两个实际好处：

1. **保证排在默认规则之后执行。** `SYMLINK` 是追加式的（多条规则可以共存），
   但 `MODE` / `GROUP` 是**后写覆盖前写** —— 放太靠前会被后面的规则改掉。
2. **一眼看出是本地加的**，不是发行版自带的。

> ⚠️ 本机 `/etc/udev/rules.d/` 里还残留旧机器人留下的 `99-xlerobot*.rules`。
> `90 < 99`，所以如果它们也对同一个 tty 设备设 `MODE` / `GROUP`，**会把我们的设置覆盖掉**。
> 不用 XLeRobot 的话建议清掉。

### 2.7 相机：`/dev/am_camera_*` 不会自己出现

`alohamini_host.py` 的相机默认路径是 `/dev/am_camera_forward` /
`/dev/am_camera_wrist_left` / `/dev/am_camera_wrist_right`，但**这几个别名不是系统自带、
也不是插上相机就会有的** —— 必须由 `91-alohamini-cameras.rules` 建立。
那套规则会给 5 路相机都建别名（`forward` / `backward` / `chest` / `wrist_left` /
`wrist_right`），其中 config 默认只启用 3 路。
没装规则时，Host 只会打一条警告把该相机跳过：

```text
以下相机不可用，已跳过：forward(/dev/am_camera_forward), ...
```

**后果**：策略收到的是全零图，看起来像「相机没接」，其实相机是好的。

#### 先确认相机到底有没有被系统认到

```bash
ls -l /dev/video*          # 有 /dev/videoN 就说明 USB 层面认到了
v4l2-ctl --list-devices
lsusb | grep -i webcam
```

#### 为什么只能按 USB 口（ID_PATH）固定，不能按序列号

本机 5 路相机都是 `0c45:64ab`（Microdia Integrated_Webcam_HD），但只有两种 iSerial
而且**组内完全重复**：机身三路都是 `ZSKJ-260319-K`，手上两路都是 `KD-231023-J`
——厂商根本没烧唯一序列号。所以 `/dev/v4l/by-id/` 会在几路相机之间互相覆盖，
`ATTRS{serial}=="..."` 也只能分出「机身组 / 手部组」，分不出左右手、也分不出躯干三路。

**结论：按物理 USB 口匹配（`ENV{ID_PATH}`）。** `ID_PATH` 由系统自带的
`60-persistent-v4l.rules`（内含 `IMPORT{builtin}="path_id"`）生成；我们的文件是 `91-`，
排在它后面，所以能取到。

查当前每个口的 ID_PATH：

```bash
for d in /dev/video*; do n=$(basename $d);
  [ "$(cat /sys/class/video4linux/$n/index)" = 0 ] || continue;
  printf '%-10s ' "$n"
  udevadm info --query=property --name=$d |
    grep -E '^(ID_PATH|ID_SERIAL)=' | tr '\n' ' '; echo
done
```

> 每个 UVC 相机会占用**两个** `/dev/videoN`：`index=0` 是取流节点，`index=1` 是 metadata。
> 只关心 `index=0`。规则里的 `ATTR{index}=="0"` 就是干这个的。

#### 安装规则

```bash
sudo cp tests/yuntao/91-alohamini-cameras.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=video4linux
ls -l /dev/am_camera_*
```

改完规则不用重启，但**已经运行的 Host 进程要重启**才会读到新别名。

> 只在装机前检查语法（不需要 root）：
> ```bash
> udevadm verify tests/yuntao/91-alohamini-cameras.rules
> ```

#### 5 路相机的物理角色（2026-10-08 机主确认）

| ID_PATH | /dev/videoN | iSerial | 物理角色 | 别名 | config 默认 |
| --- | --- | --- | --- | --- | --- |
| `platform-xhci-hcd.0-usb-0:1:1.0` | video0 | ZSKJ-260319-K | 腰部 | `am_camera_chest` | 否 |
| `platform-xhci-hcd.0-usb-0:2:1.0` | video2 | ZSKJ-260319-K | 头 | `am_camera_forward` | **是** |
| `platform-xhci-hcd.1-usb-0:2:1.0` | video4 | ZSKJ-260319-K | 背部 | `am_camera_backward` | 否 |
| `platform-xhci-hcd.1-usb-0:1.2:1.0` | video6 | KD-231023-J | 右手 | `am_camera_wrist_right` | **是** |
| `platform-xhci-hcd.1-usb-0:1.4:1.0` | video8 | KD-231023-J | 左手 | `am_camera_wrist_left` | **是** |

旁证：相机是**成组同型号**的 —— 机身三路（腰/头/背）都是 `ZSKJ-260319-K`，
手上两路（左/右）都是 `KD-231023-J`，物理角色与型号分组完全吻合。

这套名字和 `config_alohamini.py` 里注释掉的 `backward` / `chest` 条目正好对上
（「腰部 -> chest」是推断的，想改名只改规则那行的 `SYMLINK` 即可）。

#### 装完之后自检

```bash
ls -l /dev/am_camera_*     # 应出现 5 个别名

python - <<'PY'
import cv2
for cam in ("forward", "chest", "backward", "wrist_left", "wrist_right"):
    cap = cv2.VideoCapture(f"/dev/am_camera_{cam}")
    ok, f = cap.read(); cap.release()
    print(f"{cam:12s}", "OK" if ok else "FAIL")
PY
```

> 换线、换 USB 口之后重新核对：快照存 `tests/yuntao/camera_snapshots/<日期>/`，
> 每张按 ID_PATH 命名，对照表见同目录 `mapping.txt`。

#### 不想装 udev 规则的临时替代

`/dev/v4l/by-path/*-video-index0` 是系统自带的稳定软链，**不需要 root 就能直接用**，
也可以直接填进 `index_or_path`：

```text
/dev/v4l/by-path/platform-xhci-hcd.0-usb-0:2:1.0-video-index0   # forward
```

它和 `ID_PATH` 是同一套东西，换 USB 口同样会失效；好处是零配置，坏处是名字长、
不像 `/dev/am_camera_*` 那样自解释。

---

## 三、`01_lift_ssh.py` —— 升降轴（中轴）键盘遥控

### 功能

遥控升降轴（`lift_axis`，电机 ID 11，在 **left_bus** 上）。

### 按键

| 键 | 作用 |
| --- | --- |
| `W` / `K` / `U` / `↑` | 上升 |
| `S` / `J` / `↓` | 下降 |
| `Space` | 立即停止（只停运动，不退出） |
| `H` | 重新归零（会向下压到硬限位！） |
| `Q` / `ESC` | 退出 |

### 用法

```bash
# 1) 先只测键盘映射，不碰硬件
python tests/yuntao/01_lift_ssh.py --dry-run

# 2) 实测。默认先归零，然后自动抬到 400 mm 停放（避免长期压在底部）
python tests/yuntao/01_lift_ssh.py

# 3) 改停放高度 / 干脆不停放
python tests/yuntao/01_lift_ssh.py --park-mm 300
python tests/yuntao/01_lift_ssh.py --park-mm -1

# 4) 完全不想触底（线缆不允许）：不归零，当前位置即为 0mm 参考
python tests/yuntao/01_lift_ssh.py --no-home
```

### 主要参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--port` | `/dev/am_arm_follower_left` | 升降轴在 left_bus |
| `--robot-model` | `alohamini2` | 决定导程 / 升降电机型号，影响高度换算 |
| `--speed` | 600 | 按键时的原始速度（上限 `--v-max` 1300） |
| `--park-mm` | 400 | 归零后抬到该高度停放；`<0` 表示不动 |
| `--park-timeout-s` | 25.0 | 抬升到停放高度的超时 |
| `--home-speed` | 700 | 归零时的下压速度 |
| `--home-stall-current-ma` | 300 | 归零时判定“到底”的堵转电流阈值 |
| `--current-limit-ma` | 900 | 过流保护阈值 |
| `--max-height-mm` / `--descent-floor-mm` | 600 / 5.0 | 归零后的软上限 / 下降硬下限 |
| `--max-travel-mm` / `--min-travel-mm` | 550 / -5.0 | `--no-home` 时的行程预算 |
| `--loop-hz` / `--status-ms` | 50 / 100 | 主循环频率 / 状态采样间隔 |
| `--dry-run` | — | 不连硬件，只打印会下发的速度 |
| `--yes` | — | 跳过开始前的回车确认 |

### 界面

```
[UP   ] 高度=  +42.31mm  行程=  +42.31mm  电流=  180.5mA  速度= +600  按键=up
```

### 升降轴特有的坑（脚本已处理）

1. `LiftAxis.home()` 结束时会把扭矩关掉（`Torque_Enable=0`），电机自由。归零后必须
   **重新使能扭矩**，否则没有支撑、写 `Goal_Velocity` 也不动。
2. 高度需要一个零点 `z0`。`--no-home` 时高度只是**相对本次连接起点**的值，
   脚本会把 mm 软限位放开，改用「相对行程预算 + 过流 + 堵转」三重保护。
3. **压到底 = 硬堵转**，电流会冲到 ~2000 mA，舵机过载保护会让它短暂不响应
   （读位置报 `no status packet`）。脚本对所有读写都做了容错，连续失败会暂停动作而不是崩掉。
4. 升降轴掉线不安全（会因重力下滑）。退出时先把速度写 0，再**保持扭矩使能**（相当于刹车）。

---

## 四、`02_wheels_ssh.py` —— 底盘三轮键盘遥控

### 功能

遥控底盘三轮（ID 8,9,10，在 **right_bus** 上）。

### 按键

| 键 | 作用 |
| --- | --- |
| `W` / `S` | 前进 / 后退 |
| `A` / `D` | 左转 / 右转 |
| `Z` / `X` | 左平移 / 右平移 |
| `Q` / `ESC` | 退出 |

### 用法

```bash
python tests/yuntao/02_wheels_ssh.py --dry-run   # 只测键盘，不驱动电机
python tests/yuntao/02_wheels_ssh.py             # 实测
```

> ⚠️ 实测前把机器人放到有足够空间的地方，或把轮子架空。

### 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--port` | `/dev/am_arm_follower_right` | 底盘三轮在 right_bus |
| `--lin-speed` | 0.15 | 线速度上限 m/s |
| `--ang-speed` | 60 | 角速度上限 °/s |
| `--hold-ms` | 200 | 持续重复时的松开判定窗口 ms（越小停得越快） |
| `--initial-hold-ms` | 700 | 首次按下的宽容窗口 ms |
| `--dry-run` | — | 只测键盘映射，不连接 / 不驱动电机 |

### 安全性

- **SSH 掉线天然安全**：收不到按键字节，按住状态会自动过期 → 速度归零。
  这是本方案相比 `pynput` 的一个额外好处。
- 按键 `Q` / `ESC` 退出；`Ctrl+C` / `kill` / 关掉 SSH 窗口 均可立即停止。
- 退出时把速度写 0 并**保持扭矩使能**（相当于刹车），避免溜坡。

---

## 五、已知坑

- **升降轴 `home()` 结束时会关掉扭矩**（`Torque_Enable=0`），电机自由。归零后必须
  重新 `enable_torque()`，否则没有支撑、写 `Goal_Velocity` 也不动。
- **升降轴压到底 = 硬堵转**，电流会冲到 ~2000 mA，舵机过载保护会让它短暂不响应
  （读位置报 `no status packet`）。相关读写都做了容错，`home_down_speed` 已从
  1300 降到 700 减轻这个现象。
- **`AlohaMini.connect()` 每次都会把升降轴压到底归零**。线缆不够长时，
  host 侧可用 `--lift-park-mm 400`（归零后抬升）或 `--no-lift-home`（跳过归零，
  但高度会变成相对值，观测/策略会错位）。
- **`apply_action` 里的 `descent_floor_mm=5.0`** 会在未归零时拦住下降
  （因为起点恰好是 0 mm）。
- **alohamini2 的升降电机是 `sts3095`、导程 131 mm/rev**（alohamini1 才是
  `sts3215` + 84 mm/rev）。高度换算要用 `ROBOT_SPECS`；`--robot-model` 传错会让
  读数差 1.56 倍。
- **`/etc/udev/rules.d/` 里还残留 `99-xlerobot*.rules`**（旧机器人留下的），
  含 `xlerobot_cam_*` 等别名，可能与本项目冲突，XLeRobot 不用的话可以清掉。
