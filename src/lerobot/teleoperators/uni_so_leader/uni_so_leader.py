#!/usr/bin/env python

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
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

import logging
from functools import cached_property

from lerobot.types import RobotAction
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected

from ..so_leader import SOLeader, SOLeaderTeleopConfig
from ..teleoperator import Teleoperator
from .config_uni_so_leader import UniSOLeaderConfig

logger = logging.getLogger(__name__)


class UniSOLeader(Teleoperator):
    """Single SO/AM leader arm (left **or** right), keyed with a side prefix.

    Drop-in replacement for :class:`BiSOLeader` when only one arm is teleoperated: it wraps a
    single :class:`SOLeader` and prefixes every action key with ``"<side>_"`` so the keys line up
    with a single-arm AlohaMini follower (``arm_right_shoulder_pan.pos`` etc. after the recording
    loop adds its own ``arm_`` prefix).

    The calibration file id is ``<id>_<side>``, identical to the corresponding arm of
    :class:`BiSOLeader`, so an existing bimanual calibration is reused as-is.
    """

    config_class = UniSOLeaderConfig
    name = "uni_so_leader"

    def __init__(self, config: UniSOLeaderConfig):
        super().__init__(config)
        self.config = config
        if config.side not in ("left", "right"):
            raise ValueError(f"side must be 'left' or 'right', got {config.side!r}.")
        self.side = config.side

        arm_config = SOLeaderTeleopConfig(
            id=f"{config.id}_{config.side}" if config.id else None,
            calibration_dir=config.calibration_dir,
            port=config.arm_config.port,
            arm_profile=config.arm_config.arm_profile,
            use_degrees=config.arm_config.use_degrees,
        )
        self.arm = SOLeader(arm_config)

    @cached_property
    def action_features(self) -> dict[str, type]:
        return {f"{self.side}_{key}": value for key, value in self.arm.action_features.items()}

    @cached_property
    def feedback_features(self) -> dict[str, type]:
        return {}

    @property
    def is_connected(self) -> bool:
        return self.arm.is_connected

    @property
    def is_calibrated(self) -> bool:
        return self.arm.is_calibrated

    @check_if_already_connected
    def connect(self, calibrate: bool = True) -> None:
        self.arm.connect(calibrate)

    def calibrate(self) -> None:
        self.arm.calibrate()

    def configure(self) -> None:
        self.arm.configure()

    @check_if_not_connected
    def disconnect(self) -> None:
        self.arm.disconnect()

    def setup_motors(self) -> None:
        self.arm.setup_motors()

    @check_if_not_connected
    def get_action(self) -> RobotAction:
        return {f"{self.side}_{key}": value for key, value in self.arm.get_action().items()}

    def send_feedback(self, feedback: dict[str, float]) -> None:
        # TODO: Implement force feedback
        raise NotImplementedError
