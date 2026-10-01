#!/usr/bin/env python3
"""底盘 / 升降电机只读状态检查 —— 全程零写入，不改变任何电机状态。

读取内容：
  * 配置：Operating_Mode / Torque_Enable / Lock / Acceleration / Goal_Velocity
  * 实时：Position / Velocity / Load / Voltage / Temperature / Current

之所以强调「零写入」：lerobot 的 `MotorsBus.connect(handshake=False)` 只打开串口，
不写寄存器；`disconnect(disable_torque=False)` 也不会关闭扭矩。因此本脚本不会
影响电机状态，可放心在机器人上电时反复运行。

注意：`wheel_test.py --dry-run` **不是**纯只读（会写 Lock/Operating_Mode/Torque），
需要纯只读时用本脚本。

用法：
    python tests/yuntao/ro.py                                    # 检查默认目标
    python tests/yuntao/ro.py --port /dev/am_arm_follower_left   # 只查某个端口
"""

from __future__ import annotations

import argparse

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus

# 寄存器 -> (保留小数位, 缩放系数, 单位)。原始值 x 系数 = 物理量。
# Feetech 约定：电压 0.1V/LSB，电流 6.5mA/LSB，负载 0.1%/LSB。
LIVE_REGS: dict[str, tuple[int, float, str]] = {
    "Present_Position": (0, 1.0, "步"),
    "Present_Velocity": (0, 1.0, "步/s"),
    "Present_Load": (0, 0.1, "%"),
    "Present_Voltage": (1, 0.1, "V"),
    "Present_Temperature": (0, 1.0, "°C"),
    "Present_Current": (0, 6.5, "mA"),
}

SETUP_REGS = ("Operating_Mode", "Torque_Enable", "Lock", "Acceleration", "Goal_Velocity")

# 端口 -> ({电机名: ID}, 型号)。用 udev 别名而不是 /dev/ttyACM*，后者编号会漂移。
# 布局见 tests/yuntao/README.md：left = 左臂 + 升降轴；right = 右臂 + 底盘
DEFAULT_TARGETS: dict[str, tuple[dict[str, int], str]] = {
    "/dev/am_arm_follower_right": ({"left_wheel": 8, "back_wheel": 9, "right_wheel": 10}, "sts3215"),
    "/dev/am_arm_follower_left": ({"lift_axis": 11}, "sts3095"),
}


def check_port(port: str, ids: dict[str, int], model: str) -> None:
    print(f"\n{'=' * 74}\n{port}\n{'=' * 74}")
    motors = {name: Motor(i, model, MotorNormMode.RANGE_M100_100) for name, i in ids.items()}

    try:
        bus = FeetechMotorsBus(port=port, motors=motors)
        bus.connect(handshake=False)  # 仅打开端口，不写任何寄存器
    except Exception as e:
        print(f"  ⚠️ 连接失败: {type(e).__name__}: {e}")
        return

    try:
        for name in motors:
            def read(reg: str):
                try:
                    return bus.read(reg, name, normalize=False)
                except Exception:
                    return None

            setup = "  ".join(f"{reg}={read(reg)}" for reg in SETUP_REGS)
            live = "  ".join(
                f"{reg.removeprefix('Present_')}="
                + ("-" if (v := read(reg)) is None else f"{v * scale:.{dp}f}{unit}")
                for reg, (dp, scale, unit) in LIVE_REGS.items()
            )
            print(f"\n  {name} (ID {ids[name]})")
            print(f"    配置: {setup}")
            print(f"    实时: {live}")
    finally:
        bus.disconnect(disable_torque=False)  # 不改变扭矩状态


def main() -> None:
    p = argparse.ArgumentParser(description="底盘/升降电机只读状态检查（零写入）")
    p.add_argument("--port", default=None, help="只检查指定端口（默认检查全部已知端口）")
    args = p.parse_args()

    targets = (
        {args.port: DEFAULT_TARGETS[args.port]}
        if args.port in DEFAULT_TARGETS
        else ({args.port: ({f"motor_{i}": i for i in range(1, 12)}, "sts3215")} if args.port else DEFAULT_TARGETS)
    )

    for port, (ids, model) in targets.items():
        check_port(port, ids, model)

    print("\n[完成] 全程零写入，电机状态未被改变。")


if __name__ == "__main__":
    main()
