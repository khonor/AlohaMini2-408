#!/usr/bin/env python3
"""底盘三轮非交互测试 —— SSH 下可直接运行，支持毫秒级急停。

急停方式（任一，约 50 ms 内生效）：
  * 按键盘任意键
  * Ctrl+C
  * 从另一个终端 kill <pid>
  * 关闭 SSH 窗口（发 SIGHUP）
终极手段：拔电池 / 断开电机电源。

⚠️ 运行前务必把机器人架起，让轮子悬空。

用法：
    python tests/yuntao/wheel_test.py                    # 完整测试（默认 right_bus）
    python tests/yuntao/wheel_test.py --dry-run          # 只读状态，不转动

⚠️ `--dry-run` **不是零写入** —— 它仍会写 Lock / Operating_Mode / Torque_Enable。
   需要纯只读请用 `ro.py`。

底盘三轮 (8,9,10) 在 **right_bus** 上，默认端口 `/dev/am_arm_follower_right`。
别用 `/dev/ttyACM*`，编号会漂移。
"""

from __future__ import annotations

import argparse
import os
import select
import signal
import sys
import termios
import threading
import time
import tty

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode

WHEELS = {"left_wheel": 8, "back_wheel": 9, "right_wheel": 10}
MODEL = "sts3215"
SLICE_S = 0.05  # 驱动切片，决定急停延迟上限

STOP = threading.Event()
STOP_REASON = {"why": ""}


def zero_all(bus, motors) -> None:
    """把速度清零。任何异常都吞掉，保证急停路径不会二次失败。"""
    for name in motors:
        try:
            bus.write("Goal_Velocity", name, 0, normalize=False)
        except Exception:
            pass


def install_emergency_handles() -> None:
    """注册信号处理 + 启动「任意按键即停」的守护线程。"""

    def on_signal(signum, _frame):
        STOP_REASON["why"] = f"信号 {signum}({signal.Signals(signum).name})"
        STOP.set()

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, on_signal)
        except Exception:
            pass

    if not sys.stdin.isatty():
        print("[warn] stdin 不是终端，只能靠 Ctrl+C / kill / 断开连接急停")
        return

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)

    def watcher() -> None:
        try:
            tty.setcbreak(fd)  # 单字节读取，无需回车
            while not STOP.is_set():
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready:
                    os.read(fd, 64)
                    STOP_REASON["why"] = "检测到按键"
                    STOP.set()
                    return
        except Exception:
            return
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except Exception:
                pass

    threading.Thread(target=watcher, daemon=True).start()


def run_burst(bus, motors, goal: dict[str, int], seconds: float) -> bool:
    """下发一组速度并保持 seconds 秒，按 SLICE_S 切片轮询急停。返回 False = 被中断。"""
    for name, val in goal.items():
        bus.write("Goal_Velocity", name, val, normalize=False)

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if STOP.is_set():
            zero_all(bus, motors)
            return False
        time.sleep(SLICE_S)

    zero_all(bus, motors)
    time.sleep(0.3)
    return True


def raw_to_degps(raw: int) -> float:
    return raw / (4096.0 / 360.0)


def snapshot(bus, motors) -> dict[str, tuple[int, int, int]]:
    out = {}
    for name in motors:
        try:
            out[name] = (
                bus.read("Present_Position", name, normalize=False),
                bus.read("Present_Velocity", name, normalize=False),
                bus.read("Present_Current", name, normalize=False),
            )
        except Exception:
            out[name] = (-1, -1, -1)
    return out


def show(tag: str, snap: dict[str, tuple[int, int, int]]) -> None:
    print(f"\n[{tag}]")
    for name, (pos, vel, cur) in snap.items():
        print(
            f"    {name:12s} 位置={pos:6d}  速度raw={vel:6d} "
            f"({raw_to_degps(vel):7.1f} °/s)  电流={cur * 6.5:6.1f} mA"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--port",
        default="/dev/am_arm_follower_right",
        help="底盘总线（三轮 8,9,10 在 right_bus 上）；别用 /dev/ttyACM*，编号会漂移",
    )
    p.add_argument("--each-s", type=float, default=1.5, help="每个方向持续秒数")
    p.add_argument("--speed", type=int, default=1200, help="原始速度值 (4096 steps/rev)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只读一遍状态再退出，不做往返运动。注意：仍会写 Lock/Operating_Mode/Torque_Enable，不是零写入",
    )
    p.add_argument("--yes", action="store_true", help="跳过确认提示")
    args = p.parse_args()

    motors = {n: Motor(i, MODEL, MotorNormMode.RANGE_M100_100) for n, i in WHEELS.items()}

    if not args.yes:
        print("⚠️  即将驱动底盘轮子。请确认机器人已架起、轮子悬空、周围无人。")
        try:
            input("   准备好后按回车开始（Ctrl+C 取消）：")
        except KeyboardInterrupt:
            print("\n已取消。")
            return

    bus = FeetechMotorsBus(port=args.port, motors=motors)
    bus.connect(handshake=False)
    print(f"[OK] connected {args.port} @ {bus.get_baudrate()} bps")

    install_emergency_handles()

    try:
        for name in motors:
            try:
                bus.write("Lock", name, 0, normalize=False)
            except Exception as e:
                print(f"  [warn] unlock {name}: {e}")
            try:
                bus.disable_torque(name)
            except Exception:
                pass
            bus.write("Operating_Mode", name, OperatingMode.VELOCITY.value, normalize=False)
            bus.enable_torque(name)
        print("[OK] VELOCITY mode + torque ON")

        show("初始状态", snapshot(bus, motors))

        if args.dry_run:
            print("\n[dry-run] 不执行任何转动。")
            return

        plan: list[tuple[str, dict[str, int]]] = [
            ("三轮同向 正", {n: args.speed for n in motors}),
            ("三轮同向 负", {n: -args.speed for n in motors}),
        ]
        for name in motors:
            plan.append((f"仅 {name} 正转", {n: (args.speed if n == name else 0) for n in motors}))
        for name in motors:
            plan.append((f"仅 {name} 反转", {n: (-args.speed if n == name else 0) for n in motors}))

        for label, goal in plan:
            if STOP.is_set():
                break
            print(f"\n--- {label}  goal={list(goal.values())} ---")
            ok = run_burst(bus, motors, goal, args.each_s)
            show(label, snapshot(bus, motors))
            if not ok:
                print(f"\n!! 已急停：{STOP_REASON['why'] or '未知原因'}")
                break

    finally:
        zero_all(bus, motors)
        try:
            bus.disconnect(disable_torque=True)
        except Exception:
            pass
        if STOP.is_set():
            print(f"\n[DONE] 已急停并断开（{STOP_REASON['why'] or '未知原因'}）。")
        else:
            print("\n[DONE] 全部完成，轮子已停止，连接已断开。")


if __name__ == "__main__":
    main()
