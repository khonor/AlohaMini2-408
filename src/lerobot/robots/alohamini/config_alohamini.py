# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
from dataclasses import dataclass, field

from lerobot.cameras.configs import CameraConfig, Cv2Rotation
from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig

from ..config import RobotConfig

# 整机由四个**可独立启用**的部件组成。默认四个全开 = 上游整机行为。
# 只跑单臂（例如右臂，不用升降轴、不用底盘）时用 ("right_arm",)：
# 主机端不会去连接左臂/升降轴/底盘，客户端也不会把这些维度写进数据集和策略。
ALOHAMINI_PARTS: tuple[str, ...] = ("left_arm", "right_arm", "base", "lift")


def parse_enabled_parts(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """把 ``--parts`` 的输入规范化成部件名列表。

    接受 ``"all"``、逗号或空格分隔的字符串（例如 ``"right_arm"`` / ``"left_arm,right_arm"``），
    以及已经是列表/元组的取值。
    返回值始终按 ``ALOHAMINI_PARTS`` 的固定顺序排列，保证观测/动作特征的顺序稳定
    （顺序会直接决定数据集里 ``observation.state`` / ``action`` 向量的维度顺序）。
    """
    if value is None:
        return list(ALOHAMINI_PARTS)
    if isinstance(value, str):
        tokens = [token for token in re.split(r"[,\s]+", value.strip()) if token]
    else:
        tokens = [str(token).strip() for token in value if str(token).strip()]

    if not tokens or "all" in tokens:
        return list(ALOHAMINI_PARTS)

    unknown = sorted(set(tokens) - set(ALOHAMINI_PARTS))
    if unknown:
        raise ValueError(
            f"Unknown AlohaMini part(s) {unknown}. "
            f"Expected a subset of {list(ALOHAMINI_PARTS)} (or 'all')."
        )
    wanted = set(tokens)
    return [part for part in ALOHAMINI_PARTS if part in wanted]


def parse_camera_names(value: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    """把 ``--cameras`` 的输入规范化；``None`` 表示「保持配置里的全部相机」。"""
    if value is None:
        return None
    if isinstance(value, str):
        tokens = [token for token in re.split(r"[,\s]+", value.strip()) if token]
    else:
        tokens = [str(token).strip() for token in value if str(token).strip()]
    if not tokens or "all" in tokens:
        return None
    return tokens


def filter_cameras(
    cameras: dict[str, CameraConfig], names: list[str] | None
) -> tuple[dict[str, CameraConfig], list[str]]:
    """按名字筛选相机，返回 (保留的相机, 配置里不存在的名字)。"""
    if names is None:
        return dict(cameras), []
    unknown = [name for name in names if name not in cameras]
    return {name: cfg for name, cfg in cameras.items() if name in set(names)}, unknown


def alohamini_cameras_config() -> dict[str, CameraConfig]:
    # 必须显式指定 MJPG：这几路相机在 640x480 下只有 MJPG 能跑到 30fps，
    # V4L2 默认协商出来的 YUYV 上限是 25fps（`v4l2-ctl --list-formats-ext` 可查），
    # 而 OpenCVCamera._validate_fps 对不上就直接抛 RuntimeError。
    # MJPG 还顺带大幅降低 USB 带宽，挂 3 路相机时这点很关键。
    return {
        # 别名由 tests/yuntao/91-alohamini-cameras.rules 按「物理 USB 口」建立
        # （这几路相机的 USB iSerial 是重复的，不能用 /dev/v4l/by-id/）。
        # 5 路相机的物理角色：forward=头 / backward=背部 / chest=腰部 /
        #                     wrist_left=左手 / wrist_right=右手。
        # AM2 的采集与推理统一用 forward + wrist_left + wrist_right 这三路。
        "forward": OpenCVCameraConfig(
            index_or_path="/dev/am_camera_forward",
            fps=30,
            width=640,
            height=480,
            rotation=Cv2Rotation.NO_ROTATION,
            fourcc="MJPG",
        ),
        # "backward": OpenCVCameraConfig(  # 背部相机
        #     index_or_path="/dev/am_camera_backward", fps=30, width=640, height=480, rotation=Cv2Rotation.NO_ROTATION, fourcc="MJPG"
        # ),
        # "chest": OpenCVCameraConfig(  # 腰部相机
        #     index_or_path="/dev/am_camera_chest", fps=30, width=640, height=480, rotation=Cv2Rotation.NO_ROTATION, fourcc="MJPG"
        # ),
        "wrist_left": OpenCVCameraConfig(  # 左手相机
            index_or_path="/dev/am_camera_wrist_left",
            fps=30,
            width=640,
            height=480,
            rotation=Cv2Rotation.NO_ROTATION,
            fourcc="MJPG",
        ),
        "wrist_right": OpenCVCameraConfig(  # 右手相机
            index_or_path="/dev/am_camera_wrist_right",
            fps=30,
            width=640,
            height=480,
            rotation=Cv2Rotation.NO_ROTATION,
            fourcc="MJPG",
        ),
    }


@RobotConfig.register_subclass("alohamini")
@dataclass
class AlohaMiniConfig(RobotConfig):
    left_port: str = "/dev/am_arm_follower_left"  # 物理左臂所在总线：臂(1-7) + 升降轴(11)
    right_port: str = "/dev/am_arm_follower_right"  # 物理右臂所在总线：臂(1-7) + 底盘三轮(8,9,10)
    disable_torque_on_disconnect: bool = True
    # robot_model drives the whole-robot hardware specs: follower arm profile, base motors,
    # lift motor, and lead screw pitch.
    # alohamini1   – so-arm-5dof,          base sts3215, lift sts3215, lead=84 mm/rev
    # alohamini2   – am-follower-6dof,     base sts3215, lift sts3095, lead=131 mm/rev
    # alohamini2pro– am-follower-6dof-hd,  base sts3250, lift sts3095, lead=131 mm/rev
    robot_model: str = "alohamini2"

    # `max_relative_target` limits each commanded position's lead over measured
    # feedback. Keep it optional because the actuator motion profile below is the
    # primary speed/acceleration constraint.
    max_relative_target: float | dict[str, float] | None = None

    # Native Feetech position-mode motion profile, in raw register units. Keeping
    # this at the actuator preserves responsive target streaming while bounding the
    # physical motion produced by a distant or discontinuous position target.
    arm_goal_velocity: int = 2000
    arm_acceleration: int = 100

    cameras: dict[str, CameraConfig] = field(default_factory=alohamini_cameras_config)

    # Set to `True` for backward compatibility with previous policies/dataset
    use_degrees: bool = False

    # When True, skip follower arms entirely (only base and lift operate).
    # Use together with --no_leader on the teleoperate side for base-only teleoperation.
    no_follower: bool = False

    # ---------------------------------------------------------------- 部件裁剪（单臂模式）
    # 只启用整机的部分部件，默认四个全开 = 上游整机行为。
    # 例：["right_arm"] 只跑右臂 —— 左臂/升降轴/底盘既不连接、也不出现在观测与动作里。
    # 注意：底盘跟右臂挂在同一条总线上，升降轴跟左臂挂在同一条总线上（本机总线布局），
    # 所以只留 ["right_arm"] 时右总线只配置右臂的 7 个电机（底盘 ID 8/9/10 不会被寻址）。
    # no_follower=True 等价于「两条手臂都去掉」，两者可以叠加使用（no_follower 优先）。
    enabled_parts: list[str] = field(default_factory=lambda: list(ALOHAMINI_PARTS))

    # ---------------------------------------------------------------- 升降轴归零 / 停放
    # connect() 时是否把升降轴向下压到底完成归零（上游行为）。
    # 如果线缆长度不允许触底，设为 False 跳过归零。
    # ⚠️ 跳过归零后 z0 不会被重设，lift_axis.height_mm 只相对于上一次归零的位置，
    #    观测值/策略训练都会因此错位，请确认你清楚后果再关掉。
    lift_home_on_connect: bool = True

    # 归零之后自动抬到的高度（mm）。None = 就停在归零位置（最底部）不动。
    # 用来避免长期压在底部（例如线缆长度紧张）。取行程的中间偏上比较合适。
    # 行程由 LiftAxisConfig.soft_max_mm 决定（默认 600 mm）。
    lift_park_height_mm: float | None = None

    # 抬升到 lift_park_height_mm 的超时（秒）
    lift_park_timeout_s: float = 25.0

    def __post_init__(self) -> None:
        super().__post_init__()
        self.enabled_parts = parse_enabled_parts(self.enabled_parts)
        if not 1 <= self.arm_goal_velocity <= 3400:
            raise ValueError("arm_goal_velocity must be in [1, 3400].")
        if not 1 <= self.arm_acceleration <= 254:
            raise ValueError("arm_acceleration must be in [1, 254].")
        if self.lift_park_height_mm is not None and self.lift_park_height_mm < 0:
            raise ValueError("lift_park_height_mm must be >= 0 (or None to disable parking).")
        if self.lift_park_height_mm is not None and not self.lift_home_on_connect:
            raise ValueError(
                "lift_park_height_mm 需要配合 lift_home_on_connect=True 使用："
                "没有归零时高度没有绝对参考，无法可靠地移动到指定高度。"
            )


@dataclass
class AlohaMiniHostConfig:
    # Network Configuration
    port_zmq_cmd: int = 5555
    port_zmq_observations: int = 5556
    port_zmq_camera_stream: int = 5557
    observation_request_window: int = 3

    # The dedicated ROS camera stream is opt-in so the original AlohaMini Host
    # keeps its ports, CPU use, and two-camera behavior unless explicitly enabled.
    camera_stream_enabled: bool = False
    camera_stream_jpeg_quality: int = 70
    camera_stream_max_age_ms: int = 500

    # Duration of the application
    connection_time_s: int = 6000

    # Watchdog: stop the robot if no command is received for over 1 second.
    watchdog_timeout_ms: int = 1000

    # If robot jitters decrease the frequency and monitor cpu load with `top` in cmd
    max_loop_freq_hz: int = 50


@RobotConfig.register_subclass("alohamini_client")
@dataclass
class AlohaMiniClientConfig(RobotConfig):
    # Network Configuration
    remote_ip: str
    port_zmq_cmd: int = 5555
    port_zmq_observations: int = 5556
    observation_request_window: int = 3

    # Keyboard lift control advances an absolute height target while held. Bound
    # its lead over measured feedback so release and reversal stay responsive.
    lift_target_speed_mm_s: float = 150.0
    lift_target_max_lead_mm: float = 50.0

    # Must match the robot_model used on the host side so that _state_ft keys are consistent.
    # alohamini1   – so-arm-5dof (6 joints per arm, no wrist_yaw)
    # alohamini2   – am-follower-6dof (7 joints per arm, includes wrist_yaw)
    # alohamini2pro– am-follower-6dof-hd (7 joints per arm, includes wrist_yaw)
    robot_model: str = "alohamini1"

    # 必须与主机端 ``--parts`` 一致（客户端只能是主机端的子集）。
    # 例：["right_arm"] = 只录右臂：数据集/策略里不会出现左臂、底盘、升降维度。
    # 客户端会在握手时用主机端广播的 ``_robot_metadata["enabled_parts"]`` 校验，不一致直接报错。
    enabled_parts: list[str] = field(default_factory=lambda: list(ALOHAMINI_PARTS))

    teleop_keys: dict[str, str] = field(
        default_factory=lambda: {
            # Movement
            "forward": "w",
            "backward": "s",
            "left": "z",
            "right": "x",
            "rotate_left": "a",
            "rotate_right": "d",
            # Speed control
            "speed_up": "t",
            "speed_down": "g",
            # Z axis
            "lift_up": "u",
            "lift_down": "j",
            # quit teleop
            "quit": "q",
        }
    )

    cameras: dict[str, CameraConfig] = field(default_factory=alohamini_cameras_config)

    # Must exceed one host cycle at 50 Hz for request/reply observation transport.
    polling_timeout_ms: int = 200
    connect_timeout_s: int = 5

    def __post_init__(self) -> None:
        super().__post_init__()
        self.enabled_parts = parse_enabled_parts(self.enabled_parts)
