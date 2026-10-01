#!/usr/bin/env python3
"""把标定文件里的值写回两条总线的电机 EEPROM（不重新标定、不需要摆位）。

用途：等价于标定命令里那句
    "Press ENTER to use provided calibration file ... or type 'c' ..."
按回车选「使用现有标定」的那条分支。区别是本脚本是非交互的，且会**逐项回读校验**。

适用场景：电机 EEPROM 里的标定值被改坏了（例如误跑了
`set_half_turn_homings()`），而磁盘上还留着正确的标定文件，想直接恢复。

## 前置条件

* 标定文件存在（默认 `~/.cache/huggingface/lerobot/calibration/robots/alohamini/<id>.json`）
* 两条总线接好（udev 别名 `/dev/am_arm_follower_left|right` 已建立）
* 写入 EEPROM 只需解锁 Lock 寄存器，**不会关闭扭矩**，机械臂保持支撑、不会掉落。
  （脚本自带回退：万一仅解锁写不进去，会关闭扭矩重试，此时请托住手臂。）

用法：
    python tests/yuntao/restore_calibration.py                    # 默认 id=AlohaMiniRobot
    python tests/yuntao/restore_calibration.py --dry-run          # 只比对，不写入
    python tests/yuntao/restore_calibration.py --id 我的机器人 --file /path/to.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import draccus

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.motors.motors_bus import MotorCalibration
from lerobot.robots.alohamini.config_alohamini import AlohaMiniConfig
from lerobot.robots.alohamini.model_specs import ROBOT_SPECS
from lerobot.utils.constants import HF_LEROBOT_CALIBRATION, ROBOTS

# 与 alohamini.py 的 _ARM_PROFILES["am-follower-6dof"] 一致
ARM_PROFILE = (
    ("shoulder_pan", 1, "sts3095"),
    ("shoulder_lift", 2, "sts3095"),
    ("elbow_flex", 3, "sts3095"),
    ("wrist_flex", 4, "sts3215"),
    ("wrist_yaw", 5, "sts3215"),
    ("wrist_roll", 6, "sts3215"),
    ("gripper", 7, "sts3215"),
)


def build_bus_motors(cfg: AlohaMiniConfig) -> tuple[dict[str, Motor], dict[str, Motor]]:
    """按当前 alohamini.py 的布局构造两条总线的电机表。

    left_bus  = 物理左臂 + 升降轴(11)
    right_bus = 物理右臂 + 底盘三轮(8,9,10)
    """
    specs = ROBOT_SPECS[cfg.robot_model]
    bm, lm = specs["base_motor"], specs["lift_motor"]

    left_arm = {f"arm_left_{j}": Motor(i, m, MotorNormMode.RANGE_M100_100) for j, i, m in ARM_PROFILE}
    right_arm = {f"arm_right_{j}": Motor(i, m, MotorNormMode.RANGE_M100_100) for j, i, m in ARM_PROFILE}

    left = {**left_arm, "lift_axis": Motor(11, lm, MotorNormMode.DEGREES)}
    right = {
        **right_arm,
        "base_left_wheel": Motor(8, bm, MotorNormMode.RANGE_M100_100),
        "base_back_wheel": Motor(9, bm, MotorNormMode.RANGE_M100_100),
        "base_right_wheel": Motor(10, bm, MotorNormMode.RANGE_M100_100),
    }
    return left, right


def load_calibration(fpath: Path) -> dict[str, MotorCalibration]:
    with open(fpath) as f, draccus.config_type("json"):
        return draccus.load(dict[str, MotorCalibration], f)


def apply_to_bus(
    label: str,
    port: str,
    motors: dict[str, Motor],
    calibration: dict[str, MotorCalibration],
    dry_run: bool,
) -> tuple[int, int]:
    """把标定写回一条总线，并逐项回读校验。返回 (成功数, 目标数)。"""
    target = {k: v for k, v in calibration.items() if k in motors}
    missing = sorted(set(motors) - set(target))
    print(f"\n=== {label}: {port} ===")
    print(f"  该总线电机 {len(motors)} 个，标定文件中匹配到 {len(target)} 个")
    if missing:
        print(f"  ⚠️ 文件里缺少这些电机的标定: {missing}")

    bus = FeetechMotorsBus(port=port, motors=dict(motors))
    bus.connect(handshake=True)  # 顺带校验电机都在位
    try:
        if dry_run:
            cur = bus.read_calibration()
            diff = []
            for name, want in target.items():
                got = cur.get(name)
                if got is None or (got.homing_offset, got.range_min, got.range_max) != (
                    want.homing_offset,
                    want.range_min,
                    want.range_max,
                ):
                    diff.append(
                        f"    {name:26s} 电机={None if got is None else (got.homing_offset, got.range_min, got.range_max)}"
                        f"  文件={(want.homing_offset, want.range_min, want.range_max)}"
                    )
            if diff:
                print(f"  [dry-run] 以下 {len(diff)} 项与文件不一致（需要写入）:")
                print("\n".join(diff))
            else:
                print("  [dry-run] 电机里的值与文件完全一致，无需写入 ✅")
            return len(target) - len(diff), len(target)

        names = list(target)

        def _set_lock(value: int) -> None:
            for n in names:
                bus.write("Lock", n, value)

        def _verify() -> list[tuple[str, object]]:
            back = bus.read_calibration()
            bad = []
            for name, want in target.items():
                got = back.get(name)
                if got is None or (
                    got.homing_offset,
                    got.range_min,
                    got.range_max,
                ) != (
                    want.homing_offset,
                    want.range_min,
                    want.range_max,
                ):
                    bad.append((name, got))
            return bad

        # Homing_Offset 等都在 EEPROM 里，只受 Lock 寄存器保护 —— 单独解锁即可，
        # 不需要关闭扭矩，机械臂保持支撑、不会因重力掉落。
        _set_lock(0)
        try:
            bus.write_calibration(target, cache=False)
        finally:
            _set_lock(1)
        bad = _verify()

        if bad:
            print(f"  ⚠️ 仅解锁 EEPROM 时有 {len(bad)} 项没写进去，改为关闭扭矩重试（手臂请托住）…")
            bus.disable_torque()
            bus.write_calibration(target, cache=False)
            bad = _verify()

        bus.calibration = target
        for name, got in bad:
            print(f"  ❌ {name}: 写入后回读不一致 -> {got}")
        ok = len(target) - len(bad)
        print(f"  {'✅' if not bad else '⚠️'} 回读校验通过 {ok}/{len(target)}")
        return ok, len(target)
    finally:
        bus.disconnect(disable_torque=False)


def main() -> None:
    p = argparse.ArgumentParser(description="把标定文件写回电机 EEPROM（不重新标定）")
    p.add_argument("--id", default="AlohaMiniRobot", help="robot id（决定标定文件名）")
    p.add_argument("--file", type=Path, default=None, help="直接指定标定文件路径")
    p.add_argument("--dry-run", action="store_true", help="只比对，不写入")
    args = p.parse_args()

    cfg = AlohaMiniConfig()
    fpath = args.file or (
        (cfg.calibration_dir or (HF_LEROBOT_CALIBRATION / ROBOTS / "alohamini")) / f"{args.id}.json"
    )

    print(f"robot_model = {cfg.robot_model}")
    print(f"标定文件     = {fpath}")
    if not fpath.is_file():
        print("❌ 标定文件不存在。检查 --id / --file，或先用标定命令生成。")
        sys.exit(1)

    calibration = load_calibration(fpath)
    print(f"文件中电机数 = {len(calibration)}")

    left, right = build_bus_motors(cfg)
    total_ok = total_n = 0
    for label, port, motors in (
        ("left_bus (物理左臂 + 升降)", cfg.left_port, left),
        ("right_bus (物理右臂 + 底盘)", cfg.right_port, right),
    ):
        ok, n = apply_to_bus(label, port, motors, calibration, args.dry_run)
        total_ok += ok
        total_n += n

    print(f"\n{'[dry-run] ' if args.dry_run else ''}汇总: {total_ok}/{total_n} 项匹配")
    if not args.dry_run and total_ok == total_n:
        print("✅ 标定已恢复到电机。可以正常运行/校准命令了。")


if __name__ == "__main__":
    main()
