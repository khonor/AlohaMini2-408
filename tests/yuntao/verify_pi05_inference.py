#!/usr/bin/env python3
"""pi0.5（AM2 右臂）推理前自检 —— 不接硬件、不开 host。

这一步只回答一个问题：**这个 checkpoint 现在能不能被喂进推理流水线，并吐出
数量级合理的绝对关节角**。跑通了再去连机器人，否则上机就是盲试。

阶段 A（默认，秒级、不需要 GPU）：
    加载 checkpoint 的 config.json + policy_preprocessor.json / policy_postprocessor.json，
    用一条**假观测**走一遍 前处理 → (可选)策略 → 后处理，检查：
      * rename_map 是否把 forward / wrist_right 改成了 pi0.5 期望的键；
      * 相对动作缓存与还原（AbsoluteActionsProcessorStep）是否被接上；
      * 归一化统计量是否从 checkpoint 里读到了；
      * 分词器（PaliGemma）是否可用。

阶段 B（--full，需要约 10 GB 显存）：
    额外加载 9.35 GB 权重，真跑一次 predict_action_chunk，打印 50 步动作块里
    第一步相对当前 state 的偏差 —— 合理量级是「几度以内」，不是「几十度」。

用法：
    python tests/yuntao/verify_pi05_inference.py                    # 阶段 A
    python tests/yuntao/verify_pi05_inference.py --full             # A + B（cuda）
    python tests/yuntao/verify_pi05_inference.py --full --device cpu
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CKPT = REPO / "outputs/train/pi05_am2_right_arm/checkpoints/last/pretrained_model"

# 训练数据集 khonor/am2_right_arm_test 里的任务串（meta/tasks.parquet）。
# 推理时必须传一模一样的字符串，否则语言条件是训练时没见过的。
DEFAULT_TASK = "pickup1"

# dataset info.json 里 observation.state / action 的 7 个维度名。
JOINT_NAMES = [
    "arm_right_shoulder_pan.pos",
    "arm_right_shoulder_lift.pos",
    "arm_right_elbow_flex.pos",
    "arm_right_wrist_flex.pos",
    "arm_right_wrist_yaw.pos",
    "arm_right_wrist_roll.pos",
    "arm_right_gripper.pos",
]

# 训练时只用到的两路相机（第三路 left_wrist_0_rgb 是 pi05_base 的固有输入，
# 数据里没有，模型会自己补一张 mask=0 的空图）。
CAMERA_NAMES = ["forward", "wrist_right"]
IMAGE_SHAPE = (480, 640, 3)

# 数据集 khonor/am2_right_arm_test 里 observation.state 的均值，作为「一个合理姿态」。
# ⚠️ 这些 `.pos` 不是弧度，是 Feetech 的归一化行程值（大致 -100 ~ +100）。
JOINT_REF = [-17.513, 2.919, -1.926, 59.842, -10.888, -6.259, 23.173]

OK = "  [OK]  "
BAD = "  [!!]  "


def build_hw_features() -> dict:
    """复原 AlohaMiniClient(enabled_parts=['right_arm'], cameras=forward+wrist_right)
    的 observation feature 表。"""
    from lerobot.utils.feature_utils import hw_to_dataset_features

    obs_hw: dict = {name: float for name in JOINT_NAMES}
    for cam in CAMERA_NAMES:
        obs_hw[cam] = IMAGE_SHAPE
    return hw_to_dataset_features(obs_hw, "observation")


def fake_observation(state: list[float]) -> dict:
    """一条假的「机器人刚读到的原始观测」：7 个关节角 + 两路 uint8 图。"""
    obs = {name: float(v) for name, v in zip(JOINT_NAMES, state)}
    # 用带点纹理的假图，避免全 0 图和「相机坏了」混淆。
    for i, cam in enumerate(CAMERA_NAMES):
        img = np.zeros(IMAGE_SHAPE, dtype=np.uint8)
        img[:, :, i] = np.linspace(0, 255, IMAGE_SHAPE[1], dtype=np.uint8)[None, :]
        obs[cam] = img
    return obs


def build_batch(ckpt: Path, task: str, device: str, state: list[float]) -> tuple[dict, dict]:
    """走一遍真实推理路径：build_dataset_frame → prepare_observation_for_inference。

    跟 rollout/inference/rtc.py 的取数方式保持一致。
    """
    from lerobot.policies.utils import prepare_observation_for_inference
    from lerobot.utils.feature_utils import build_dataset_frame

    hw_features = build_hw_features()
    frame = build_dataset_frame(hw_features, fake_observation(state), prefix="observation")
    batch = prepare_observation_for_inference(frame, torch.device(device), task, "alohamini_client")
    # RTC 引擎就是这么塞 task 的（rollout/inference/rtc.py）
    batch["task"] = [task]
    return batch, hw_features


def report_state_distribution(preprocessor, state: list[float]) -> list[str]:
    """把这次用的关节角跟 checkpoint 里的训练分位数对比。

    pi0.5 是把 state 离散化后写进提示词的，**一旦超出训练分布**，
    离散化会饱和到边界 bin，模型看到的就是一个它没见过的输入。
    这是上机前最值得先看一眼的数字。
    """
    from lerobot.processor import NormalizerProcessorStep

    normalizer = next((s for s in preprocessor.steps if isinstance(s, NormalizerProcessorStep)), None)
    if normalizer is None or "observation.state" not in getattr(normalizer, "stats", {}):
        print("    （checkpoint 里没读到 observation.state 的统计量，跳过分布检查）")
        return []

    stats = normalizer.stats["observation.state"]
    print(f"    stats 里可用的键: {sorted(stats.keys())}")
    lo = stats.get("q01")
    hi = stats.get("q99")
    if lo is None or hi is None:
        lo, hi = stats.get("min"), stats.get("max")
    if lo is None or hi is None:
        return []

    print(f"\n    {'关节':<34}{'本次取值':>10}{'训练q01':>10}{'训练q99':>10}   ")
    out_of_range = []
    for i, name in enumerate(JOINT_NAMES):
        v, a, b = state[i], float(lo[i]), float(hi[i])
        flag = "" if a <= v <= b else "  <== 超出训练分布"
        if flag:
            out_of_range.append(name)
        print(f"    {name:<34}{v:10.3f}{a:10.3f}{b:10.3f}{flag}")
    if out_of_range:
        print(f"    ⚠️  {len(out_of_range)} 个维度超出训练 q01~q99：{', '.join(out_of_range)}")
    else:
        print("    ✅ 7 个维度都在训练 q01~q99 之内")
    return out_of_range


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ckpt", type=Path, default=DEFAULT_CKPT, help=f"默认 {DEFAULT_CKPT}")
    parser.add_argument("--task", type=str, default=DEFAULT_TASK, help="必须与训练数据集的任务串一致")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--full", action="store_true", help="额外加载 9.35 GB 权重真跑一次前向")
    parser.add_argument(
        "--state",
        type=str,
        default=None,
        help="逗号分隔的 7 个关节角（.pos 归一化行程值，约 -100~100）。"
        "不传则用训练数据集的均值。上机前把机器人摆到起始位后，"
        "可以用它先确认姿态是否落在训练分布内。"
        '注意负值开头要写成 --state="-80,-35,..."，否则 argparse 会把它当成选项。',
    )
    args = parser.parse_args()

    if args.state:
        args.state = [float(v) for v in args.state.replace(" ", "").split(",")]
        if len(args.state) != len(JOINT_NAMES):
            parser.error(f"--state 需要 {len(JOINT_NAMES)} 个值，收到 {len(args.state)} 个")
    else:
        args.state = list(JOINT_REF)

    ckpt: Path = args.ckpt.resolve()
    failures: list[str] = []
    warnings: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print((OK if cond else BAD) + msg)
        if not cond:
            failures.append(msg)

    print(f"\n=== checkpoint: {ckpt}")
    if not (ckpt / "config.json").is_file():
        print(BAD + "找不到 config.json，--ckpt 指错了吗？")
        return 1

    cfg_json = json.loads((ckpt / "config.json").read_text())
    print(f"    type={cfg_json['type']}  chunk_size={cfg_json['chunk_size']}  "
          f"n_action_steps={cfg_json['n_action_steps']}")
    print(f"    use_relative_actions={cfg_json['use_relative_actions']}  "
          f"relative_exclude_joints={cfg_json['relative_exclude_joints']}")
    print(f"    action_feature_names={cfg_json['action_feature_names']}")

    # ------------------------------------------------------------------ 阶段 A
    print("\n=== 阶段 A：处理器流水线（不加载权重）")
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.pretrained import PreTrainedConfig

    policy_cfg = PreTrainedConfig.from_pretrained(str(ckpt))
    policy_cfg.pretrained_path = str(ckpt)

    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=policy_cfg,
        pretrained_path=str(ckpt),
        dataset_stats=None,  # 归一化统计量应当从 checkpoint 的 safetensors 里读
        preprocessor_overrides={"device_processor": {"device": "cpu"}},
    )
    names_in = [type(s).__name__ for s in preprocessor.steps]
    names_out = [type(s).__name__ for s in postprocessor.steps]
    print(f"    pre : {names_in}")
    print(f"    post: {names_out}")

    # 相对动作的「配对」是反序列化后手动接回来的（policies/factory.py:_reconnect_relative_absolute_steps）
    from lerobot.processor import AbsoluteActionsProcessorStep, RelativeActionsProcessorStep

    rel = next((s for s in preprocessor.steps if isinstance(s, RelativeActionsProcessorStep)), None)
    abs_step = next((s for s in postprocessor.steps if isinstance(s, AbsoluteActionsProcessorStep)), None)
    check(rel is not None and rel.enabled, "preprocessor 里有启用中的 RelativeActionsProcessorStep")
    check(abs_step is not None and abs_step.enabled, "postprocessor 里有启用中的 AbsoluteActionsProcessorStep")
    check(abs_step is not None and abs_step.relative_step is rel,
          "Absolute 与 Relative 步骤已配对（否则推理时无法把相对动作还原成绝对动作）")
    if rel is not None:
        mask = rel._build_mask(len(JOINT_NAMES))
        print(f"    relative mask={mask}  (False = 保持绝对，应为最后一维 gripper)")
        check(mask[:-1] == [True] * 6 and mask[-1] is False,
              "gripper 维度被排除在相对动作之外（与训练一致）")

    batch, hw_features = build_batch(ckpt, args.task, "cpu", args.state)
    print(f"    假观测键: {sorted(k for k in batch if k.startswith('observation'))}")

    with torch.inference_mode():
        prepared = preprocessor(batch)

    keys = set(prepared.keys())
    check("observation.images.base_0_rgb" in keys,
          "forward 被 rename 成 observation.images.base_0_rgb")
    check("observation.images.right_wrist_0_rgb" in keys,
          "wrist_right 被 rename 成 observation.images.right_wrist_0_rgb")
    check("observation.images.forward" not in keys, "原来的 forward 键已被消费掉")
    # pi0.5 不把 state 当向量喂进网络：它把 state 用 256 个 bin 离散化后**写进提示词**
    # （processor_pi05.Pi05PrepareStateTokenizerProcessorStep），所以这里 state 仍是 7 维，
    # 真正要检查的是「归一化 + 离散化 + 任务串拼接」走通了。
    if "observation.state" in keys:
        st = prepared["observation.state"]
        print(f"    observation.state -> {tuple(st.shape)}  "
              f"(归一化后: {[round(v, 3) for v in st.flatten().tolist()]})")

    print("\n    --- state 是否落在训练分布内 ---")
    out_of_range = report_state_distribution(preprocessor, args.state)
    if "observation.state" in keys:
        check(bool((prepared["observation.state"].abs() <= 1.0 + 1e-4).all()),
              "state 归一化后落在 [-1, 1]（超出说明取值超出了训练分布）")
    if out_of_range:
        # 不判失败：q01~q99 只是 98% 的数据范围，起点略靠外不一定就废；
        # 但这是上机前最该先看到的数字，所以单独列出来。
        warnings.append(
            f"起点有 {len(out_of_range)} 个关节超出训练 q01~q99：{', '.join(out_of_range)}"
        )

    prompt = prepared.get("task")
    if isinstance(prompt, list):
        prompt = prompt[0]
    print(f"\n    提示词: {prompt!r}")
    check(isinstance(prompt, str) and prompt.startswith(f"Task: {args.task}, State:"),
          f"提示词以 'Task: {args.task}, State:' 开头（任务串必须与训练一致）")
    if isinstance(prompt, str) and "State:" in prompt:
        digits = prompt.split("State:")[1].split(";")[0].split()
        in_bins = all(d.lstrip("-").isdigit() and -1 <= int(d) <= 256 for d in digits)
        check(len(digits) == len(JOINT_NAMES) and in_bins,
              "7 个关节角都被离散化进了提示词")
    tok_key = next((k for k in keys if "language" in k and "token" in k), None)
    check(tok_key is not None, f"分词成功（{tok_key}）")
    # predict_action_chunk 里是 batch[OBS_LANGUAGE_TOKENS] + batch[OBS_LANGUAGE_ATTENTION_MASK] 成对取用的，
    # 少一个阶段 B 会直接 KeyError。
    mask_key = next((k for k in keys if "language" in k and "attention" in k), None)
    check(mask_key is not None, f"attention mask 也在（{mask_key}）")
    print(f"    前处理后全部键: {sorted(keys)}")

    if not args.full:
        print("\n阶段 A 结束。加 --full 可真跑一次策略前向（需要约 10 GB 显存）。")
        return report(failures, warnings)

    # ------------------------------------------------------------------ 阶段 B
    print(f"\n=== 阶段 B：加载权重并前向（device={args.device}）")
    from lerobot.policies import get_policy_class

    policy_class = get_policy_class(policy_cfg.type)
    policy = policy_class.from_pretrained(str(ckpt), config=policy_cfg)
    policy = policy.to(args.device)
    policy.eval()

    # ⚠️ 必须先把**原始观测**过一遍 preprocessor（rename → 归一化 → 分词 → device），
    # 否则 policy 拿到的还是 observation.images.forward / wrist_right 这两个键，
    # _preprocess_images 会认为三路图全缺，直接抛
    #   ValueError: All image features are missing from the batch.
    # 阶段 A 检查的是 preprocessor 的输出，阶段 B 这里以前漏了这一步。
    raw_batch, _ = build_batch(ckpt, args.task, args.device, args.state)
    state = raw_batch["observation.state"].cpu().float().flatten()[: len(JOINT_NAMES)]
    batch = preprocessor(raw_batch)
    # 阶段 A 为了不占显存，preprocessor 的 device_processor 被覆盖成了 cpu；
    # 阶段 B 要把 policy 的输入显式搬到目标设备，否则 token 在 cpu、权重在 cuda：
    #   RuntimeError: Expected all tensors to be on the same device ...
    batch = {
        k: (v.to(args.device) if torch.is_tensor(v) else v) for k, v in batch.items()
    }
    with torch.inference_mode():
        chunk = policy.predict_action_chunk(batch)
        absolute = postprocessor(chunk.cpu())

    print(f"    predict_action_chunk -> {tuple(chunk.shape)}   (期望 (1, {policy_cfg.chunk_size}, 7))")
    check(chunk.shape[1] == policy_cfg.chunk_size and chunk.shape[2] == len(JOINT_NAMES),
          "动作块形状与 chunk_size / 7 维动作一致")

    first = absolute.squeeze(0).float()[0]
    last = absolute.squeeze(0).float()[-1]
    print("\n    维度                                   state     a[0]      a[49]")
    for i, name in enumerate(JOINT_NAMES):
        print(f"      {name:<34} {state[i]:8.3f} {first[i]:8.3f} {last[i]:8.3f}")
    delta0 = (first - state).abs()
    print(f"\n    |a[0] - state| 最大偏差 = {delta0.max():.3f}")
    check(bool(torch.isfinite(absolute).all()), "动作全为有限值（没有 NaN/inf）")
    check(float(delta0.max()) < 30.0,
          "动作块第一步与当前 state 同量级（>30 说明相对/绝对还原或归一化有问题）")
    return report(failures, warnings)


def report(failures: list[str], warnings: list[str]) -> int:
    print("\n" + "=" * 62)
    for w in warnings:
        print(f"警告：{w}")
    if failures:
        print(f"结果：{len(failures)} 项未通过")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("结果：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
