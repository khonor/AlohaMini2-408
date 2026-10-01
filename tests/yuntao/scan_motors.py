#!/usr/bin/env python3
"""串口电机扫描 —— 探测某端口在各波特率下响应的电机 ID 与型号。

用途：排查「电机没响应」时，先确认这个端口上到底挂了哪些 ID、型号对不对。

输出示例：
    /dev/am_arm_follower_right @ 1000000 bps -> {1: 2569, 2: 2569, ..., 8: 777, 9: 777, 10: 777}
    型号码：777=sts3215  2569=sts3095  2825=sts3250

注意：扫描只做 broadcast_ping（读操作），`set_baudrate` 只改**主机端口**的波特率，
不会写入电机 EEPROM，因此是安全的。

用法：
    python tests/yuntao/scan_motors.py                                   # 扫描两个默认端口
    python tests/yuntao/scan_motors.py --port /dev/am_arm_follower_left
"""

from __future__ import annotations

import argparse

from lerobot.motors.feetech import FeetechMotorsBus

MODEL_NAMES = {
    777: "sts3215",
    2569: "sts3095",
    2825: "sts3250",
    11272: "sm8512bl",
    1284: "scs0009",
}

# 用 udev 别名而不是 /dev/ttyACM*，后者编号会漂移
DEFAULT_PORTS = ["/dev/am_arm_follower_left", "/dev/am_arm_follower_right"]


def scan(port: str) -> dict[int, list[int]]:
    print(f"\n{'=' * 60}\n扫描 {port}\n{'=' * 60}")
    try:
        result = FeetechMotorsBus.scan_port(port)
    except Exception as e:
        print(f"  ⚠️ 扫描失败: {type(e).__name__}: {str(e)[:200]}")
        return {}

    if not result:
        print(f"  ❌ {port}: 任何波特率下都没有电机响应")
        return result

    for baud, ids in result.items():
        print(f"  ✅ {port} @ {baud} bps -> 电机 ID = {sorted(ids)}")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="扫描串口上响应的电机 ID")
    p.add_argument("--port", action="append", default=None, help="要扫描的端口，可重复；默认两条总线（udev 别名）")
    args = p.parse_args()

    for port in args.port or DEFAULT_PORTS:
        scan(port)

    print("\n型号码对照：" + "  ".join(f"{k}={v}" for k, v in MODEL_NAMES.items()))


if __name__ == "__main__":
    main()
