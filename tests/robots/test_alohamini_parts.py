#!/usr/bin/env python

"""单臂（部件裁剪）模式：host 端 / 客户端 / 主臂 / 录制动作组装。"""

from types import SimpleNamespace

import pytest

from lerobot.robots.alohamini.alohamini import AlohaMini
from lerobot.robots.alohamini.alohamini_client import AlohaMiniClient
from lerobot.robots.alohamini.alohamini_host import build_robot_metadata
from lerobot.robots.alohamini.config_alohamini import (
    ALOHAMINI_PARTS,
    AlohaMiniClientConfig,
    AlohaMiniConfig,
    filter_cameras,
    parse_camera_names,
    parse_enabled_parts,
)
from lerobot.teleoperators.so_leader import SOLeaderConfig
from lerobot.teleoperators.uni_so_leader import UniSOLeader, UniSOLeaderConfig

RIGHT_ARM_KEYS = (
    "arm_right_shoulder_pan.pos",
    "arm_right_shoulder_lift.pos",
    "arm_right_elbow_flex.pos",
    "arm_right_wrist_flex.pos",
    "arm_right_wrist_yaw.pos",
    "arm_right_wrist_roll.pos",
    "arm_right_gripper.pos",
)


def make_host_robot(parts, model="alohamini2") -> AlohaMini:
    """A real AlohaMini whose camera drivers are never started."""
    config = AlohaMiniConfig(robot_model=model, enabled_parts=list(parts))
    config.cameras = {}
    return AlohaMini(config)


class RecordingBus:
    def __init__(self, motors=()) -> None:
        self.motors = dict.fromkeys(motors)
        self.reads: list[str] = []
        self.writes: list[tuple[str, dict]] = []

    def sync_read(self, register, motors):
        self.reads.append(register)
        return dict.fromkeys(motors, 1.0)

    def sync_write(self, register, values, **kwargs):
        self.writes.append((register, dict(values)))


# --------------------------------------------------------------------------- parts parsing
def test_parse_enabled_parts_normalizes_order_and_rejects_unknown() -> None:
    assert parse_enabled_parts("all") == list(ALOHAMINI_PARTS)
    assert parse_enabled_parts(None) == list(ALOHAMINI_PARTS)
    assert parse_enabled_parts("right_arm") == ["right_arm"]
    # 顺序永远按 ALOHAMINI_PARTS 固定，数据集里的维度顺序才不会随命令行变化。
    assert parse_enabled_parts("lift,right_arm") == ["right_arm", "lift"]
    assert parse_enabled_parts(["right_arm", "right_arm"]) == ["right_arm"]
    with pytest.raises(ValueError, match="Unknown AlohaMini part"):
        parse_enabled_parts("right_leg")
    with pytest.raises(ValueError, match="Unknown AlohaMini part"):
        AlohaMiniConfig(enabled_parts=["arms"])


def test_parse_and_filter_cameras() -> None:
    cameras = {"forward": "f", "wrist_left": "l", "wrist_right": "r"}
    assert parse_camera_names("all") is None
    assert parse_camera_names(None) is None
    assert parse_camera_names("forward, wrist_right") == ["forward", "wrist_right"]

    kept, unknown = filter_cameras(cameras, parse_camera_names("forward,wrist_right"))
    assert list(kept) == ["forward", "wrist_right"]
    assert unknown == []

    kept, unknown = filter_cameras(cameras, parse_camera_names("forward,chest"))
    assert list(kept) == ["forward"]
    assert unknown == ["chest"]

    kept, unknown = filter_cameras(cameras, None)
    assert kept == cameras and unknown == []


# --------------------------------------------------------------------------- host robot
def test_right_arm_only_host_drops_left_bus_base_and_lift() -> None:
    robot = make_host_robot(["right_arm"])

    assert robot.enabled_parts == ("right_arm",)
    assert robot.left_bus is None
    assert robot.base_motors == []
    assert robot.lift.enabled is False
    assert list(robot._state_ft) == list(RIGHT_ARM_KEYS)
    assert list(robot.action_features) == list(RIGHT_ARM_KEYS)
    # 只给右臂的 7 个电机建了电流保护（不再包含左臂/底盘/升降）。
    assert set(robot._current_limits) == {key.removesuffix(".pos") for key in RIGHT_ARM_KEYS}


def test_full_host_keeps_the_upstream_schema() -> None:
    robot = make_host_robot(["all"])
    keys = list(robot._state_ft)

    assert robot.enabled_parts == ALOHAMINI_PARTS
    assert len(keys) == 18
    assert keys[-4:] == ["x.vel", "y.vel", "theta.vel", "lift_axis.height_mm"]


def test_no_follower_still_means_base_plus_lift() -> None:
    config = AlohaMiniConfig(robot_model="alohamini2", no_follower=True)
    config.cameras = {}
    robot = AlohaMini(config)

    assert robot.enabled_parts == ("base", "lift")
    assert robot.left_arm_motors == [] and robot.right_arm_motors == []
    assert list(robot._state_ft) == ["x.vel", "y.vel", "theta.vel", "lift_axis.height_mm"]


def test_right_arm_only_observation_has_no_base_or_lift_fields() -> None:
    robot = make_host_robot(["right_arm"])
    right_bus = RecordingBus()
    robot.right_bus = right_bus
    robot.base_bus = right_bus
    robot.read_and_check_currents = lambda **_kwargs: {}
    robot.logs = {}

    observation = AlohaMini.get_observation.__wrapped__(robot, include_cameras=False)

    assert sorted(key for key in observation if not key.startswith("_")) == sorted(RIGHT_ARM_KEYS)
    assert right_bus.reads == ["Present_Position"]


def test_right_arm_only_send_action_never_touches_base_or_lift() -> None:
    robot = make_host_robot(["right_arm"])
    right_bus = RecordingBus(motor.removesuffix(".pos") for motor in RIGHT_ARM_KEYS)
    robot.right_bus = right_bus
    robot.base_bus = right_bus
    robot.config.max_relative_target = None
    robot._limit_gripper_goal_by_current = lambda _bus, goals: goals
    robot._limit_joint_goal_by_current = lambda _bus, goals: goals
    robot.logs = {}

    action = dict.fromkeys(RIGHT_ARM_KEYS, 1.5)
    # 客户端在单臂模式下根本不会发 x.vel / lift_axis.height_mm，这里只发右臂也应成立。
    sent = AlohaMini.send_action.__wrapped__(robot, action)

    assert [register for register, _ in right_bus.writes] == ["Goal_Position"]
    written = right_bus.writes[0][1]
    assert written == {key.removesuffix(".pos"): 1.5 for key in RIGHT_ARM_KEYS}
    assert set(sent) >= set(RIGHT_ARM_KEYS)
    assert "x.vel" not in written and "lift_axis" not in written


def test_host_metadata_advertises_enabled_parts() -> None:
    robot = make_host_robot(["right_arm"])
    metadata = build_robot_metadata(robot)

    assert metadata["enabled_parts"] == ["right_arm"]
    assert metadata["robot_model"] == "alohamini2"


# --------------------------------------------------------------------------- client
def make_client(parts, *, model="alohamini2", cameras=None) -> AlohaMiniClient:
    config = AlohaMiniClientConfig(
        remote_ip="127.0.0.1",
        id="test",
        robot_model=model,
        enabled_parts=list(parts),
        cameras={} if cameras is None else cameras,
    )
    return AlohaMiniClient(config)


def test_right_arm_only_client_exposes_seven_dimensions() -> None:
    client = make_client(["right_arm"])

    assert client.enabled_parts == ("right_arm",)
    assert client.use_base is False and client.use_lift is False
    assert list(client.action_features) == list(RIGHT_ARM_KEYS)
    assert client._state_order == RIGHT_ARM_KEYS


def test_full_client_exposes_the_upstream_schema() -> None:
    client = make_client(["all"])

    assert len(client.action_features) == 18
    assert list(client._state_order)[-4:] == ["x.vel", "y.vel", "theta.vel", "lift_axis.height_mm"]


def test_client_rejects_a_host_that_does_not_cover_its_parts() -> None:
    right_only = make_client(["right_arm"])

    # 客户端要的部件必须被 Host 覆盖；Host 多开部件是允许的（客户端会忽略多余的 key）。
    right_only._validate_host_metadata({"robot_model": "alohamini2", "enabled_parts": ["right_arm"]})
    right_only._validate_host_metadata(
        {"robot_model": "alohamini2", "enabled_parts": ["left_arm", "right_arm", "base", "lift"]}
    )
    # 老版本 Host 不广播 enabled_parts：跳过校验，保持向后兼容。
    right_only._validate_host_metadata({"robot_model": "alohamini2"})
    right_only._validate_host_metadata({})

    with pytest.raises(ValueError, match="missing"):
        make_client(["all"])._validate_host_metadata(
            {"robot_model": "alohamini2", "enabled_parts": ["right_arm"]}
        )
    with pytest.raises(ValueError, match="robot_model"):
        right_only._validate_host_metadata({"robot_model": "alohamini2pro", "enabled_parts": []})


# --------------------------------------------------------------------------- teleop
def make_uni_leader(side: str) -> UniSOLeader:
    return UniSOLeader(
        UniSOLeaderConfig(
            arm_config=SOLeaderConfig(port="/dev/null", arm_profile="am-leader-6dof"),
            side=side,
            id="am_leader_bi",
        )
    )


def test_uni_so_leader_prefixes_one_arm_only() -> None:
    leader = make_uni_leader("right")
    leader.arm.get_action = lambda: {"shoulder_pan.pos": 1.0, "gripper.pos": 2.0}

    assert UniSOLeader.get_action.__wrapped__(leader) == {
        "right_shoulder_pan.pos": 1.0,
        "right_gripper.pos": 2.0,
    }
    assert list(leader.action_features) == [
        "right_shoulder_pan.pos",
        "right_shoulder_lift.pos",
        "right_elbow_flex.pos",
        "right_wrist_flex.pos",
        "right_wrist_yaw.pos",
        "right_wrist_roll.pos",
        "right_gripper.pos",
    ]


def test_uni_so_leader_reuses_the_bimanual_right_calibration_id() -> None:
    assert make_uni_leader("right").arm.id == "am_leader_bi_right"
    assert make_uni_leader("left").arm.id == "am_leader_bi_left"


def test_uni_so_leader_rejects_an_unknown_side() -> None:
    with pytest.raises(ValueError, match="side"):
        make_uni_leader("middle")


# --------------------------------------------------------------------------- record loop
def test_build_teleop_action_skips_disabled_parts() -> None:
    from examples.alohamini.record_utils import build_teleop_action

    leader = SimpleNamespace(get_action=lambda: {"right_shoulder_pan.pos": 1.0})

    class Robot:
        def __init__(self, *, use_base: bool, use_lift: bool) -> None:
            self.use_base = use_base
            self.use_lift = use_lift
            self.base_calls = 0
            self.lift_calls = 0

        def _from_keyboard_to_base_action(self, _keys):
            self.base_calls += 1
            return {"x.vel": 0.1, "y.vel": 0.0, "theta.vel": 0.0}

        def _from_keyboard_to_lift_action(self, _keys):
            self.lift_calls += 1
            return {"lift_axis.height_mm": 100.0}

    full = Robot(use_base=True, use_lift=True)
    assert build_teleop_action(full, leader, {}) == {
        "arm_right_shoulder_pan.pos": 1.0,
        "x.vel": 0.1,
        "y.vel": 0.0,
        "theta.vel": 0.0,
        "lift_axis.height_mm": 100.0,
    }
    assert (full.base_calls, full.lift_calls) == (1, 1)

    right_only = Robot(use_base=False, use_lift=False)
    assert build_teleop_action(right_only, leader, {}) == {"arm_right_shoulder_pan.pos": 1.0}
    assert (right_only.base_calls, right_only.lift_calls) == (0, 0)
