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

from dataclasses import dataclass

from ..config import TeleoperatorConfig
from ..so_leader import SOLeaderConfig


@TeleoperatorConfig.register_subclass("uni_so_leader")
@dataclass
class UniSOLeaderConfig(TeleoperatorConfig):
    """Configuration class for a single-arm SO/AM leader (left or right side only).

    ``BiSOLeader`` drives both leader arms and prefixes every key with ``left_``/``right_``.
    This is the single-arm equivalent: it drives exactly one leader and prefixes its keys with
    ``config.side`` so the resulting keys match an AlohaMini follower that runs in single-arm
    mode (``--parts right_arm``).
    """

    arm_config: SOLeaderConfig
    # "right" or "left" —— 决定动作 key 的前缀（arm_right_* / arm_left_*）。
    side: str = "right"
