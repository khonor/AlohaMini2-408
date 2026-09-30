#!/usr/bin/env python

import argparse
import logging

from .alohamini import AlohaMini
from .config_alohamini import AlohaMiniConfig


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate AlohaMini and exit")
    parser.add_argument(
        "--robot_model",
        type=str,
        default="alohamini1",
        choices=["alohamini1", "alohamini2", "alohamini2pro"],
        help=(
            "Robot model. Must match the physical AlohaMini hardware: "
            "alohamini1, alohamini2, or alohamini2pro."
        ),
    )
    parser.add_argument(
        "--no_follower",
        action="store_true",
        help="Skip follower arm calibration.",
    )
    parser.add_argument(
        "--id",
        type=str,
        default="AlohaMiniRobot",
        help="Robot ID used for the calibration file.",
    )
    return parser


def main():
    args = make_parser().parse_args()

    logging.info("Configuring AlohaMini for calibration")
    robot_config = AlohaMiniConfig()
    robot_config.id = args.id
    robot_config.robot_model = args.robot_model
    robot_config.no_follower = args.no_follower
    # 校准只涉及电机，不需要相机。清空相机配置，避免因为 /dev/am_camera_* 别名
    # 尚未建立（或相机未接入）导致 connect() 直接失败。
    robot_config.cameras = {}

    robot = AlohaMini(robot_config)

    try:
        logging.info("Connecting AlohaMini without auto-calibration")
        robot.connect(calibrate=False)
        robot.calibrate()
        if robot.is_calibrated:
            robot.lift.home()
            print("Lift axis homed to 0mm.")
        print("AlohaMini calibration complete.")
    finally:
        if robot.is_connected:
            robot.disconnect()


if __name__ == "__main__":
    main()
