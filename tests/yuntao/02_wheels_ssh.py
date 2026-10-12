#!/usr/bin/env python3
"""底盘键盘遥控 —— SSH 专用版，不需要 X / pynput / VNC。

## 为什么需要这个脚本

`examples/debug/wheels.py` 用 `pynput` 抓全局键盘，但 pynput 只能走 X11。
本机是 Wayland(labwc)，SSH 会话里 `DISPLAY` 为空 -> 导入即报错；
即使在树莓派本机显示器上运行，Wayland 原生终端也不会把按键事件交给 XWayland，
结果是脚本正常运行、却永远收不到按键。

## 本脚本的原理

用终端**原始模式(cbreak)** 直接读控制终端的字节（复用 lerobot 的
`TerminalKeyListener`），并用**按键自动重复**模拟「按住」：

    按住 W 不放 -> 终端持续发送重复字节 -> 每次刷新 W 的时间戳
    超过窗口时间没再收到 -> 判定为已松开

终端只能给出「按下」事件（没有「松开」事件），所以这是个近似方案。

**双窗口自适应**：系统自动重复有个「首次延迟」（250~660ms），之后才以 30~50ms
的间隔连续重复。若只用一个窗口，按下瞬间会因等不到第一次重复而误判松开，出现
「动一下 -> 卡住 -> 又动」的顿挫。因此这里用两个窗口：

    * `--initial-hold-ms`（默认 700）—— 刚按下时用，需大于系统的首次延迟
    * `--hold-ms`（默认 200）—— 重复已连续后用，松开后能迅速停下

由此带来一个**固有取舍**：**轻点一下**（来不及产生任何重复字节）时，脚本无法区分
「你已经松手」和「系统的首次延迟还没到」，因此那一次点按会带来最多
`--initial-hold-ms` 的缓动（默认 700ms）。想要即点即停，就把系统的键盘重复延迟
调短（Windows: 设置->键盘；X11: `xset r rate 200 40`），再相应减小 `--initial-hold-ms`。

## 安全性

* **网络/SSH 掉线天然安全**：没有按键字节到达，按住状态会自动过期 -> 速度归零。
  这是本方案相比 pynput 的一个额外好处。
* 按键 `q` 或 `ESC` 退出；`Ctrl+C` / `kill` / 关掉 SSH 窗口 均可立即停止。
* 退出时会把速度写 0 并**保持扭矩使能**（相当于刹车），避免机器人溜坡。

## 用法

⚠️ **不要用 `conda run -n lerobot_alohamini python ...`**：它会用管道接管
stdin，`sys.stdin.isatty()` 变成 False，读不到按键（而且会缓冲 stdout，
界面不实时刷新）。改用下面任一方式：

    conda run --no-capture-output -n lerobot_alohamini python tests/yuntao/02_wheels_ssh.py
    # 或者
    conda activate lerobot_alohamini && python tests/yuntao/02_wheels_ssh.py

    python tests/yuntao/02_wheels_ssh.py
    python tests/yuntao/02_wheels_ssh.py --dry-run   # 只测键盘，不驱动电机

底盘三轮(8,9,10)在 **right_bus** 上，默认端口 `/dev/am_arm_follower_right`。
别用 `/dev/ttyACM*` —— 编号会漂移。

按键：
    W / S   前进 / 后退
    A / D   左转 / 右转
    Z / X   左平移 / 右平移
    Q / ESC 退出
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

import numpy as np

# lerobot.motors 会连带导入 torch，实测启动要 ~5.4 秒。
# 在重导入之前先打一行提示，否则用户会以为脚本卡死了。
print("正在加载 lerobot 依赖（含 torch，约 5-6 秒）…", file=sys.stderr, flush=True)

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.utils.keyboard_input import TerminalKeyListener

# ------------------------ 常量（与 examples/debug/wheels.py 保持一致） ------------------------ #

WHEELS: dict[str, int] = {"left_wheel": 8, "back_wheel": 9, "right_wheel": 10}
MODEL = "sts3215"

WHEEL_RADIUS = 0.05  # m
BASE_RADIUS = 0.125  # m
MAX_RAW = 3000  # 原始速度上限

KEY_TO_ACTION: dict[str, str] = {
    "w": "forward",
    "s": "backward",
    "z": "left",
    "x": "right",
    "a": "rotate_left",
    "d": "rotate_right",
}
QUIT_KEYS = {"q", "esc"}

LOOP_HZ = 50.0


def open_controlling_tty():
    """返回一个可读的控制终端文件对象；取不到返回 None。

    用途：`conda run -n <env> python ...` 会用**管道**接管 stdin，于是
    `sys.stdin.isatty()` 变成 False，按键读不到。但控制终端 `/dev/tty` 仍然可用，
    直接打开它就能恢复键盘输入。（同时也顺带解决了 stdin 被重定向的情况。）
    """
    try:
        f = open("/dev/tty", "r")  # noqa: SIM115 - 由调用方持有到进程结束
    except OSError:
        return None
    if not f.isatty():
        f.close()
        return None
    return f


# ------------------------ 运动学 ------------------------ #


def degps_to_raw(degps: float) -> int:
    """角速度 (deg/s) -> steps/s 的有符号 16 位编码（与官方 lekiwi 一致）。"""
    steps_per_deg = 4096.0 / 360.0
    mag = int(round(abs(degps) * steps_per_deg))
    mag = min(mag, 0x7FFF)
    return -mag if degps < 0 else mag


def body_to_wheel_raw(
    x_cmd: float,
    y_cmd: float,
    theta_cmd_degps: float,
    *,
    wheel_radius: float = WHEEL_RADIUS,
    base_radius: float = BASE_RADIUS,
    max_raw: int = MAX_RAW,
) -> dict[str, int]:
    """机体速度 -> 各轮原始速度。x/y 单位 m/s，theta 单位 deg/s。"""
    theta_rad = theta_cmd_degps * (np.pi / 180.0)
    vel = np.array([-x_cmd, -y_cmd, theta_rad])

    angles = np.radians(np.array([240, 0, 120]) - 90)
    M = np.array([[np.cos(a), np.sin(a), base_radius] for a in angles])

    v_lin = M.dot(vel)
    w_degps = (v_lin / wheel_radius) * (180.0 / np.pi)

    raw_abs = np.abs(w_degps) * (4096.0 / 360.0)
    peak = float(np.max(raw_abs)) if raw_abs.size else 0.0
    if peak > max_raw and peak > 1e-6:
        w_degps = w_degps * (max_raw / peak)

    raw = [degps_to_raw(v) for v in w_degps]
    return {"left_wheel": raw[0], "back_wheel": raw[1], "right_wheel": raw[2]}


# ------------------------ 遥控主体 ------------------------ #


class SshTeleop:
    def __init__(
        self,
        port: str,
        lin_speed: float,
        ang_speed: float,
        hold_ms: float,
        initial_hold_ms: float,
        dry_run: bool,
    ):
        self.port = port
        self.lin_speed = lin_speed
        self.ang_speed = ang_speed
        self.hold_s = hold_ms / 1000.0
        self.initial_hold_s = initial_hold_ms / 1000.0
        self.dry_run = dry_run

        self._lock = threading.Lock()
        # action -> (最近一次收到的时间, 连续收到次数)
        self._state: dict[str, tuple[float, int]] = {}
        self._quit = False
        self._stop = threading.Event()

        self.bus: FeetechMotorsBus | None = None

    # ---- 键盘回调（运行在 TerminalKeyListener 的守护线程上） ----
    def _on_key(self, key: str) -> None:
        if key in QUIT_KEYS:
            self._quit = True
            return
        action = KEY_TO_ACTION.get(key)
        if action is None:
            return
        now = time.monotonic()
        with self._lock:
            last, count = self._state.get(action, (0.0, 0))
            # 距上次超过当前窗口 -> 视为新一轮按下，计数重置
            window = self.initial_hold_s if count <= 1 else self.hold_s
            self._state[action] = (now, count + 1 if now - last < window else 1)

    def _held(self) -> set[str]:
        now = time.monotonic()
        held: set[str] = set()
        with self._lock:
            for action, (last, count) in self._state.items():
                window = self.initial_hold_s if count <= 1 else self.hold_s
                if now - last < window:
                    held.add(action)
                else:
                    self._state[action] = (last, 0)  # 已松开，重置计数
        return held

    # ---- 硬件 ----
    def connect(self) -> None:
        motors = {n: Motor(i, MODEL, MotorNormMode.RANGE_M100_100) for n, i in WHEELS.items()}
        self.bus = FeetechMotorsBus(port=self.port, motors=motors)
        self.bus.connect(handshake=False)
        print(f"[OK] 已连接 {self.port} @ {self.bus.get_baudrate()} bps")

        for name in motors:
            try:
                self.bus.write("Lock", name, 0, normalize=False)
            except Exception:
                pass
            try:
                self.bus.disable_torque(name)
            except Exception:
                pass
            self.bus.write("Operating_Mode", name, OperatingMode.VELOCITY.value, normalize=False)
            self.bus.enable_torque(name)
        print("[OK] 三轮已进入 VELOCITY 模式并开启扭矩")

    def zero(self) -> None:
        if self.bus is None:
            return
        for name in WHEELS:
            try:
                self.bus.write("Goal_Velocity", name, 0, normalize=False)
            except Exception:
                pass

    def close(self) -> None:
        self.zero()  # 先归零
        if self.bus is not None:
            try:
                # 保持扭矩使能 = 主动刹车，避免机器人溜坡
                self.bus.disconnect(disable_torque=False)
            except Exception:
                pass

    # ---- 主循环 ----
    def run(self) -> None:
        if not sys.stdin.isatty():
            tty = open_controlling_tty()
            if tty is None:
                print(
                    "[ERROR] 读不到终端，无法接收按键。\n"
                    "        请直接在 SSH 交互式终端里运行，不要重定向输入（< file / | ...）。",
                    file=sys.stderr,
                )
                return
            print(
                "[WARN] stdin 不是终端 —— 最常见的原因是用 `conda run` 启动，\n"
                "       它会用管道接管 stdin（也会缓冲 stdout，导致界面不实时刷新）。\n"
                "       本次已自动改用 /dev/tty 读按键，但强烈建议换成下面任一方式：\n"
                "           conda run --no-capture-output -n lerobot_alohamini \\\n"
                "               python tests/yuntao/02_wheels_ssh.py\n"
                "           conda activate lerobot_alohamini && \\\n"
                "               python tests/yuntao/02_wheels_ssh.py\n",
                file=sys.stderr,
                flush=True,
            )
            sys.stdin = tty  # TerminalKeyListener 读的就是 sys.stdin

        listener = TerminalKeyListener(self._on_key)
        listener.start()

        print("\n按键： W/S 前后  A/D 转向  Z/X 平移  Q/ESC 退出")
        print(f"速度： 线速度 ±{self.lin_speed} m/s   角速度 ±{self.ang_speed} °/s")
        print(
            f"松开判定： 首次按下后 {self.initial_hold_s * 1000:.0f} ms / 持续重复后 "
            f"{self.hold_s * 1000:.0f} ms\n"
        )

        period = 1.0 / LOOP_HZ
        last_print = 0.0

        try:
            while not self._quit and not self._stop.is_set():
                held = self._held()

                x = self.lin_speed * (("forward" in held) - ("backward" in held))
                y = self.lin_speed * (("left" in held) - ("right" in held))
                th = self.ang_speed * (("rotate_left" in held) - ("rotate_right" in held))

                cmd = body_to_wheel_raw(x, y, th)

                if not self.dry_run and self.bus is not None:
                    for name, val in cmd.items():
                        self.bus.write("Goal_Velocity", name, val, normalize=False)

                now = time.monotonic()
                if now - last_print > 0.2:
                    last_print = now
                    tag = " ".join(sorted(held)) or "-"
                    vals = " ".join(f"{n.split('_')[0]}={v:6d}" for n, v in cmd.items())
                    prefix = "[dry] " if self.dry_run else ""
                    print(f"\r{prefix}按键={tag:34s} {vals}", end="", flush=True)

                time.sleep(period)
        except KeyboardInterrupt:
            print("\n[中断]")
        finally:
            print()
            listener.stop()
            self.zero()
            print("[OK] 速度已归零")


def main() -> None:
    p = argparse.ArgumentParser(description="底盘键盘遥控（SSH 可用，无需 X/pynput）")
    p.add_argument(
        "--port",
        default="/dev/am_arm_follower_right",
        help="底盘总线端口。注意：底盘三轮(8,9,10)在 right_bus 上，"
        "不要用 /dev/ttyACM*（编号会漂移）",
    )
    p.add_argument("--lin-speed", type=float, default=0.15, help="线速度上限 m/s，默认 0.15")
    p.add_argument("--ang-speed", type=float, default=60.0, help="角速度上限 °/s，默认 60")
    p.add_argument(
        "--hold-ms",
        type=float,
        default=200.0,
        help="持续重复时的松开判定窗口 ms，默认 200（越小停得越快）",
    )
    p.add_argument(
        "--initial-hold-ms",
        type=float,
        default=700.0,
        help="首次按下后的宽容窗口 ms，默认 700。终端自动重复的首次延迟通常是 "
        "250~660ms，此值需大于该延迟，否则刚按下时会一顿一顿",
    )
    p.add_argument("--dry-run", action="store_true", help="只测键盘映射，不连接/不驱动电机")
    args = p.parse_args()

    if args.hold_ms < 60:
        p.error("--hold-ms 至少 60，否则自动重复会被误判为松开")
    if args.initial_hold_ms < args.hold_ms:
        p.error("--initial-hold-ms 应 >= --hold-ms")

    teleop = SshTeleop(
        args.port,
        args.lin_speed,
        args.ang_speed,
        args.hold_ms,
        args.initial_hold_ms,
        args.dry_run,
    )

    def on_signal(signum, _frame):
        print(f"\n[信号 {signum}({signal.Signals(signum).name})] 停止中…")
        teleop._stop.set()
        teleop._quit = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, on_signal)
        except Exception:
            pass

    if not args.dry_run:
        print("⚠️  请确认机器人已架起/轮子悬空，周围无人。")
        try:
            input("   准备好后按回车开始（Ctrl+C 取消）：")
        except KeyboardInterrupt:
            print("\n已取消。")
            return
        teleop.connect()

    try:
        teleop.run()
    finally:
        teleop.close()
        print("[DONE]")


if __name__ == "__main__":
    main()
