#!/usr/bin/env python3
"""判断哪条机械臂挂在哪个串口 —— 用于区分校准时的 LEFT / RIGHT arm。

## 为什么需要它

校准脚本里的 "LEFT arm" 指的是 **left_port** 上那条臂，而 left_port 在本机被
配置成 `/dev/ttyACM1`（和底盘三轮共板的那条）。如果两条臂接反了，校准照样能跑完，
但之后主从臂会左右错位。

## 原理

两条臂的电机 ID 完全一样（都是 1-7），所以不能靠 ID 区分。但它们在**不同总线**上，
所以你手动推动任意一条臂时，**只有它所在的那条总线**的位置读数会变化。

## 安全

* 全程**只读**位置，不下发任何运动指令。
* 需要该臂扭矩处于关闭状态才能手动推动。脚本会先检测：
  - 若已关闭 -> 直接开始，让你推。
  - 若仍使能 -> 会**警告并等你确认**，然后才关闭扭矩（手臂可能因重力下落，请先托住）。

用法：
    python tests/yuntao/identify_arms.py
"""

from __future__ import annotations

import argparse
import time

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig

# 与 alohamini.py 的 _ARM_PROFILES["am-follower-6dof"] 一致
ARM_JOINTS: tuple[tuple[str, int, str], ...] = (
    ("shoulder_pan", 1, "sts3095"),
    ("shoulder_lift", 2, "sts3095"),
    ("elbow_flex", 3, "sts3095"),
    ("wrist_flex", 4, "sts3215"),
    ("wrist_yaw", 5, "sts3215"),
    ("wrist_roll", 6, "sts3215"),
    ("gripper", 7, "sts3215"),
)

# 位置变化超过这个原始计数(4096/圈)才算"被推动了"
MOVE_THRESHOLD = 30


def open_bus(port: str) -> FeetechMotorsBus:
    motors = {f"j{j}": Motor(i, m, MotorNormMode.RANGE_M100_100) for j, i, m in ARM_JOINTS}
    bus = FeetechMotorsBus(port=port, motors=motors)
    bus.connect(handshake=False)  # 不校验、不写寄存器
    return bus


def read_positions(bus: FeetechMotorsBus) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in bus.motors:
        try:
            out[name] = int(bus.read("Present_Position", name, normalize=False))
        except Exception:
            out[name] = -1
    return out


def torque_state(bus: FeetechMotorsBus) -> bool:
    """任一臂电机的扭矩是否处于使能状态。"""
    for name in bus.motors:
        try:
            if bus.read("Torque_Enable", name, normalize=False):
                return True
        except Exception:
            pass
    return False


def disable_arm_torque(bus: FeetechMotorsBus) -> None:
    for name in bus.motors:
        try:
            bus.disable_torque(name)
        except Exception as e:
            print(f"  [warn] 关闭 {name} 扭矩失败: {e}")


def main() -> None:
    p = argparse.ArgumentParser(description="判断哪条臂挂在哪个串口")
    p.add_argument("--hold-s", type=float, default=0.0, help="每次推动后的额外等待秒数")
    args = p.parse_args()

    cfg = AlohaMiniConfig()
    ports = {"left_port": cfg.left_port, "right_port": cfg.right_port}

    print("正在打开两条总线（只读）…")
    buses: dict[str, FeetechMotorsBus] = {}
    for label, port in ports.items():
        try:
            buses[label] = open_bus(port)
            print(f"  ✅ {label:10s} = {port}")
        except Exception as e:
            print(f"  ❌ {label:10s} = {port} 打开失败: {type(e).__name__}: {e}")

    if len(buses) < 2:
        print("\n⚠️ 两条总线没有都打开成功，无法比较。")
        for b in buses.values():
            b.disconnect(disable_torque=False)
        return

    try:
        # ---- 1. 检查扭矩：必须先关闭才能手动推动 ----
        need = [lbl for lbl, b in buses.items() if torque_state(b)]
        if need:
            print(f"\n⚠️  以下总线的臂电机扭矩仍处于**使能**状态：{', '.join(need)}")
            print("    手动推不动。关闭扭矩后手臂可能因重力下落 ——")
            print("    👉 请先用一只手托住任一条臂，再按回车继续（或 Ctrl+C 取消）。")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                print("\n已取消。")
                return
            for lbl in need:
                disable_arm_torque(buses[lbl])
            print("  已关闭臂电机扭矩。")
        else:
            print("\n✅ 臂电机扭矩已关闭，可以直接手动推动。")

        # ---- 2. 记录基线 ----
        base = {lbl: read_positions(b) for lbl, b in buses.items()}
        print("\n基线位置（关节: 原始计数）:")
        for lbl in buses:
            joined = "  ".join(f"{k}={v}" for k, v in base[lbl].items())
            print(f"  {lbl:10s} ({ports[lbl]}): {joined}")

        # ---- 3. 等用户推动 ----
        print("\n" + "=" * 66)
        print("请**只推动其中一条臂**，随便转动任意一个关节（比如肘部或腕部），")
        print("幅度大一点（转个几十度），推完按回车。")
        print("=" * 66)
        try:
            input("\n推完后按回车 > ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return
        if args.hold_s:
            time.sleep(args.hold_s)

        # ---- 4. 比较 ----
        after = {lbl: read_positions(b) for lbl, b in buses.items()}
        moved: dict[str, list[str]] = {}
        for lbl in buses:
            changed = [
                k
                for k in base[lbl]
                if base[lbl][k] >= 0 and after[lbl][k] >= 0 and abs(after[lbl][k] - base[lbl][k]) > MOVE_THRESHOLD
            ]
            moved[lbl] = changed

        print("\n检测结果:")
        for lbl in buses:
            delta = "  ".join(
                f"{k}:{base[lbl][k]}->{after[lbl][k]}" for k in moved[lbl]
            )
            mark = "★ 有变化" if moved[lbl] else "  无变化"
            print(f"  {mark}  {lbl:10s} ({ports[lbl]})  {delta}")

        active = [lbl for lbl, c in moved.items() if c]
        header = "  " + "=" * 62
        print(f"\n{header}")
        if len(active) == 1:
            lbl = active[0]
            arm = "LEFT arm（左臂）" if lbl == "left_port" else "RIGHT arm（右臂）"
            print(f"  ✅ 你推动的是挂在 {ports[lbl]} 上的那条臂")
            print(f"     → 校准脚本会把它当作 {arm}")
        elif not active:
            print("  ❓ 没检测到位移变化。可能是：")
            print("     - 推的幅度太小（试试转 90° 以上）")
            print("     - 该臂扭矩仍使能，推不动")
            print("     - 读数失败（看看上面基线里有没有 -1）")
        else:
            print(f"  ⚠️ 两条总线都检测到变化（{active}），无法区分。请重跑并只推一条臂。")
        print(header)

    finally:
        for b in buses.values():
            try:
                b.disconnect(disable_torque=False)  # 不改动扭矩状态
            except Exception:
                pass


if __name__ == "__main__":
    main()
