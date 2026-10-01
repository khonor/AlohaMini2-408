#!/usr/bin/env python3
"""校验 alohamini 的「总线 ↔ 电机」布局是否与实机一致。

背景：上游设计的布局是 `left_bus = 左臂 + 底盘 + 升降`（三者在同一条总线上），
`right_bus = 右臂`。但本机实机把底盘和升降**分在了两条总线**上，且经
`identify_arms.py` 实测确认了物理左右：

    /dev/am_arm_follower_left  (serial 5B91044456) = 物理左臂(1-7) + 升降(11, sts3095)
    /dev/am_arm_follower_right (serial 5B90148934) = 物理右臂(1-7) + 底盘(8,9,10, sts3215)

因此 `alohamini.py` 把**底盘挂到右总线**、**升降留在左总线**，并引入 `self.base_bus`
统一指向底盘所在的总线（所有底盘读写都走它，不再写死 left_bus）；
`config_alohamini.py` 则使用 udev 固定名以抵抗 /dev/ttyACM* 编号漂移。

本脚本按**当前代码**构造出两条总线的期望电机表，然后以 `handshake=True` 连接，
触发 `_assert_motors_exist()` —— 这正是校准命令报错的那一步。

安全性：`connect(handshake=True)` 只读取型号做校验，`disconnect(disable_torque=False)`
不改扭矩。全程零写入。

用法：
    python tests/yuntao/check_bus_layout.py
"""

from __future__ import annotations

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig
from lerobot.robots.alohamini.model_specs import ROBOT_SPECS

# 与 alohamini.py 里的 _ARM_PROFILES 保持一致（(关节名, ID, 型号, 归一化模式)）
ARM_PROFILES: dict[str, tuple[tuple[str, int, str], ...]] = {
    "am-follower-6dof": (
        ("shoulder_pan", 1, "sts3095"),
        ("shoulder_lift", 2, "sts3095"),
        ("elbow_flex", 3, "sts3095"),
        ("wrist_flex", 4, "sts3215"),
        ("wrist_yaw", 5, "sts3215"),
        ("wrist_roll", 6, "sts3215"),
        ("gripper", 7, "sts3215"),
    ),
}


def build_bus_motors(cfg: AlohaMiniConfig) -> tuple[dict[str, Motor], dict[str, Motor]]:
    """按 alohamini.py 的逻辑构造 left/right 两条总线的电机表。"""
    specs = ROBOT_SPECS[cfg.robot_model]
    bm, lm = specs["base_motor"], specs["lift_motor"]
    profile = ARM_PROFILES[specs["arm_profile"]]

    left_arm = {f"arm_left_{j}": Motor(i, m, MotorNormMode.RANGE_M100_100) for j, i, m in profile}
    right_arm = {f"arm_right_{j}": Motor(i, m, MotorNormMode.RANGE_M100_100) for j, i, m in profile}

    # 本机实测布局（与上游默认不同）：
    #   left_bus  = 左臂 + 升降轴(11)
    #   right_bus = 右臂 + 底盘三轮(8,9,10)
    left = {
        **left_arm,
        "lift_axis": Motor(11, lm, MotorNormMode.DEGREES),
    }
    right = {
        **right_arm,
        "base_left_wheel": Motor(8, bm, MotorNormMode.RANGE_M100_100),
        "base_back_wheel": Motor(9, bm, MotorNormMode.RANGE_M100_100),
        "base_right_wheel": Motor(10, bm, MotorNormMode.RANGE_M100_100),
    }
    return left, right


def check(name: str, port: str, motors: dict[str, Motor]) -> bool:
    expect = {m.id: m.model for m in motors.values()}
    print(f"\n=== {name}: {port} ===")
    print(f"  期望电机: {dict(sorted(expect.items()))}")

    bus = FeetechMotorsBus(port=port, motors=dict(motors))
    try:
        bus.connect(handshake=True)  # 只做存在性校验，不写寄存器
    except Exception as e:
        print(f"  ❌ 校验失败: {type(e).__name__}")
        for line in str(e).splitlines():
            print(f"     {line}")
        return False
    else:
        print("  ✅ 全部电机就位")
        return True
    finally:
        try:
            bus.disconnect(disable_torque=False)  # 不改扭矩状态
        except Exception:
            pass


def main() -> None:
    cfg = AlohaMiniConfig()
    print(f"robot_model = {cfg.robot_model}")
    left, right = build_bus_motors(cfg)

    ok_left = check("left_bus", cfg.left_port, left)
    ok_right = check("right_bus", cfg.right_port, right)

    print()
    if ok_left and ok_right:
        print("✅ 两条总线布局与实机完全一致，校准命令的电机检查应该能通过。")
    else:
        print("❌ 布局与实机不符，请对照上方「期望电机」与实机扫描结果修正。")


if __name__ == "__main__":
    main()
