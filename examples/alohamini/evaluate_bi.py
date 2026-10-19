#!/usr/bin/env python3

import argparse
import inspect
import logging
import math
import time

import lerobot.robots.alohamini  # noqa: F401 — registers alohamini_client robot type
from lerobot.configs.policies import PreTrainedConfig
from lerobot.datasets import aggregate_pipeline_dataset_features, create_initial_features
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.policies import get_policy_class, make_pre_post_processors
from lerobot.policies.rtc.configuration_rtc import RTCConfig
from lerobot.processor import make_default_processors
from lerobot.robots.alohamini import AlohaMiniClient, AlohaMiniClientConfig
from lerobot.robots.alohamini.config_alohamini import (
    alohamini_cameras_config,
    filter_cameras,
    parse_camera_names,
    parse_enabled_parts,
)
from lerobot.rollout.inference.factory import (
    RTCInferenceConfig,
    SyncInferenceConfig,
    create_inference_engine,
)
from lerobot.rollout.robot_wrapper import ThreadSafeRobot
from lerobot.utils.action_interpolator import ActionInterpolator
from lerobot.utils.constants import ACTION, OBS_STR
from lerobot.utils.device_utils import auto_select_torch_device
from lerobot.utils.feature_utils import build_dataset_frame, combine_feature_dicts, hw_to_dataset_features
from lerobot.utils.robot_utils import precise_sleep
from lerobot.utils.utils import log_say

try:
    from .evaluation_safety import EvaluationSafetyGuard, hold_action, stop_inference
except ImportError:
    from evaluation_safety import EvaluationSafetyGuard, hold_action, stop_inference


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value

    value = value.lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("Expected true or false.")


def main():
    parser = argparse.ArgumentParser(description="Evaluate AlohaMini robot with a pretrained policy")
    parser.add_argument("--eval.n_episodes", "--num_episodes", dest="num_episodes", type=int, default=2)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--eval.episode_time_s", "--episode_time", dest="episode_time", type=int, default=60)
    parser.add_argument(
        "--eval.reset_time_s",
        "--reset_time",
        dest="reset_time",
        type=float,
        default=10.0,
        help="Reset duration between evaluation episodes in seconds.",
    )
    parser.add_argument(
        "--eval.reset_home",
        dest="reset_home",
        type=str,
        default="",
        help=(
            "逗号分隔的关节目标姿态。非空时，**每一集开始前**先把被控制的关节平滑地"
            "送回这个姿态再开跑。π0.5 是模仿学习，训练数据每一集都从同一个起始姿态示教，"
            "从别的姿态起步会让 state 离散化饱和、策略迅速跑飞。"
            "⚠️ 值以负号开头，**必须用等号写法**，否则 argparse 会把 -2.4... 当成选项："
            '  --eval.reset_home="-2.451,-0.355,2.863,65.328,-8.801,-0.568,5.456"'
        ),
    )
    parser.add_argument(
        "--eval.reset_home_max_speed",
        dest="reset_home_max_speed",
        type=float,
        default=30.0,
        help="回位时的最大关节速度（Feetech 归一化单位/秒），越小越慢越安全。",
    )
    parser.add_argument(
        "--dataset.single_task",
        "--task_description",
        dest="task_description",
        type=str,
        default="robot task",
    )
    parser.add_argument("--policy.path", "--hf_model_id", dest="policy_path", type=str, required=True)
    parser.add_argument(
        "--dataset.repo_id", "--hf_dataset_id", dest="dataset_repo_id", type=str, required=True
    )
    parser.add_argument(
        "--dataset.push_to_hub",
        dest="push_to_hub",
        type=parse_bool,
        nargs="?",
        const=True,
        default=True,
        help="Whether to upload the evaluation dataset to Hugging Face Hub.",
    )
    parser.add_argument("--robot.remote_ip", "--remote_ip", dest="remote_ip", type=str, default="127.0.0.1")
    parser.add_argument("--robot.id", "--robot_id", dest="robot_id", type=str, default="my_alohamini")
    parser.add_argument(
        "--robot.robot_model",
        "--robot_model",
        dest="robot_model",
        type=str,
        default="alohamini1",
        choices=["alohamini1", "alohamini2", "alohamini2pro"],
        help="Must match the robot_model on the Pi host side",
    )
    parser.add_argument(
        "--robot.parts",
        "--robot_parts",
        dest="robot_parts",
        type=str,
        default="all",
        help=(
            "AlohaMini parts to drive: all (default) or a comma-separated subset of "
            "left_arm,right_arm,base,lift. Must match the Host's --parts; use right_arm to run a "
            "single-arm (right) policy."
        ),
    )
    parser.add_argument(
        "--robot.cameras",
        "--robot_cameras",
        dest="robot_cameras",
        type=str,
        default="all",
        help=(
            "Comma-separated camera names, default all. Must match the Host's --cameras and the "
            "cameras the policy was trained on."
        ),
    )
    parser.add_argument(
        "--inference.type",
        "--inference_type",
        dest="inference_type",
        type=str,
        default="sync",
        choices=["sync", "rtc"],
        help="sync: one policy call per control tick. rtc: async background inference "
        "(only for policies whose predict_action_chunk supports inference_delay/prev_chunk_left_over, "
        "e.g. pi0/pi05/pi0_fast/smolvla/evo1/molmoact2 — not act/diffusion/vqbet).",
    )
    parser.add_argument(
        "--inference.rtc.execution_horizon",
        dest="rtc_execution_horizon",
        type=int,
        default=10,
        help="RTC only: number of steps re-generated per inference call.",
    )
    parser.add_argument(
        "--inference.rtc.max_guidance_weight",
        dest="rtc_max_guidance_weight",
        type=float,
        default=10.0,
        help="RTC only: max guidance weight for prefix inpainting.",
    )
    parser.add_argument(
        "--inference.rtc.queue_threshold",
        dest="rtc_queue_threshold",
        type=int,
        default=30,
        help="RTC only: trigger a new inference call once the action queue drops to this size.",
    )
    parser.add_argument(
        "--interpolation_multiplier",
        dest="interpolation_multiplier",
        type=int,
        default=1,
        help="Send N interpolated actions per policy action for a smoother, higher-rate control loop "
        "(1 = disabled).",
    )
    args = parser.parse_args()
    if args.reset_time < 0:
        parser.error("--eval.reset_time_s must be non-negative")

    device = str(auto_select_torch_device())

    # === Policy ===
    policy_cfg = PreTrainedConfig.from_pretrained(args.policy_path)
    policy_cfg.pretrained_path = args.policy_path
    policy_class = get_policy_class(policy_cfg.type)
    policy = policy_class.from_pretrained(args.policy_path, config=policy_cfg)

    # === Inference engine config (needs to be set up before .to(device)/.eval(), mirroring
    # lerobot.rollout.context.build_rollout_context) ===
    if args.inference_type == "rtc":
        predict_chunk_params = inspect.signature(policy_class.predict_action_chunk).parameters
        accepts_rtc_kwargs = {"inference_delay", "prev_chunk_left_over"}.issubset(
            predict_chunk_params
        ) or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in predict_chunk_params.values())
        if not accepts_rtc_kwargs:
            raise ValueError(
                f"Policy type '{policy_cfg.type}' does not support RTC inference: "
                f"predict_action_chunk() has no inference_delay/prev_chunk_left_over/**kwargs. "
                f"RTC is only supported by VLA-style policies (pi0, pi05, pi0_fast, smolvla, evo1, "
                f"molmoact2). Use --inference.type=sync instead."
            )
        rtc_config = RTCConfig(
            execution_horizon=args.rtc_execution_horizon,
            max_guidance_weight=args.rtc_max_guidance_weight,
        )
        policy.config.rtc_config = rtc_config
        if hasattr(policy, "init_rtc_processor"):
            policy.init_rtc_processor()
        inference_cfg = RTCInferenceConfig(rtc=rtc_config, queue_threshold=args.rtc_queue_threshold)
    else:
        inference_cfg = SyncInferenceConfig()

    policy = policy.to(device)
    policy.eval()

    # === Robot ===
    # 单臂策略要用 --robot.parts right_arm + --robot.cameras，并且与主机端 --parts/--cameras 一致，
    # 否则数据集特征/动作维度会和策略训练时对不上。
    try:
        enabled_parts = parse_enabled_parts(args.robot_parts)
    except ValueError as e:
        parser.error(str(e))
    cameras, unknown_cameras = filter_cameras(
        alohamini_cameras_config(), parse_camera_names(args.robot_cameras)
    )
    if unknown_cameras:
        parser.error(
            f"--robot.cameras 里有配置中不存在的名字：{', '.join(unknown_cameras)}；"
            f"可用：{', '.join(sorted(alohamini_cameras_config()))}"
        )
    robot_config = AlohaMiniClientConfig(
        remote_ip=args.remote_ip,
        id=args.robot_id,
        robot_model=args.robot_model,
        enabled_parts=enabled_parts,
        cameras=cameras,
    )
    robot = AlohaMiniClient(robot_config)
    robot.connect()
    robot_wrapper = ThreadSafeRobot(robot)

    # === Processors ===
    teleop_action_processor, robot_action_processor, robot_observation_processor = make_default_processors()

    # === Dataset features ===
    # Use all observation features (pos + base vel/height) to match what record_bi.py records.
    observation_features_hw = robot.observation_features
    action_features_hw = robot.action_features

    action_dataset_features = aggregate_pipeline_dataset_features(
        pipeline=teleop_action_processor,
        initial_features=create_initial_features(action=action_features_hw),
        use_videos=True,
    )
    observation_dataset_features = aggregate_pipeline_dataset_features(
        pipeline=robot_observation_processor,
        initial_features=create_initial_features(observation=observation_features_hw),
        use_videos=True,
    )
    dataset_features = combine_feature_dicts(action_dataset_features, observation_dataset_features)
    hw_features = hw_to_dataset_features(observation_features_hw, "observation")
    ordered_action_keys = list(action_features_hw.keys())

    # === Dataset ===
    dataset = LeRobotDataset.create(
        repo_id=args.dataset_repo_id,
        fps=args.fps,
        features=dataset_features,
        robot_type=robot.name,
        use_videos=True,
        image_writer_threads=4,
    )

    # === Policy processors (needs dataset stats) ===
    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=policy_cfg,
        pretrained_path=args.policy_path,
        dataset_stats=dataset.meta.stats,
        preprocessor_overrides={"device_processor": {"device": device}},
    )

    # === Inference engine ===
    engine = create_inference_engine(
        inference_cfg,
        policy=policy,
        preprocessor=preprocessor,
        postprocessor=postprocessor,
        robot_wrapper=robot_wrapper,
        hw_features=hw_features,
        dataset_features=dataset_features,
        ordered_action_keys=ordered_action_keys,
        task=args.task_description,
        fps=float(args.fps),
        device=device,
    )

    # init_rerun(session_name="alohamini_evaluate")
    log_say("Starting evaluation")

    interpolator = ActionInterpolator(multiplier=args.interpolation_multiplier)
    control_interval = interpolator.get_control_interval(args.fps)
    recorded = 0
    safety_guard = EvaluationSafetyGuard()

    # ---------------------------------------------------------------- 回位目标
    reset_home_target: dict[str, float] = {}
    if args.reset_home:
        values = [float(v) for v in args.reset_home.replace(" ", "").split(",") if v != ""]
        if len(values) != len(ordered_action_keys):
            logging.warning(
                "--eval.reset_home 给了 %d 个值，但这次有 %d 个被控关节；回位功能已禁用。",
                len(values),
                len(ordered_action_keys),
            )
        else:
            reset_home_target = dict(zip(ordered_action_keys, values, strict=True))

    def return_home(tag: str = "") -> None:
        """把被控关节以限速方式平滑送回 reset_home_target。

        为什么要限速：起始姿态可能离目标很远，一次性跳过去会让机械臂猛冲。
        这里按 ``args.reset_home_max_speed`` 算步数，每步只前进一个很小的量。
        """
        if not reset_home_target:
            return

        obs = robot.get_observation()
        current = {k: float(v) for k, v in obs.items() if k in reset_home_target}
        missing = [k for k in reset_home_target if k not in current]
        if missing:
            # 客户端发出去的动作包必须是完整的：少一个键会被 host 整包拒绝。
            logging.warning("回位：观测里缺少关节 %s，跳过回位。", ", ".join(missing))
            return

        max_delta = max(abs(reset_home_target[k] - current[k]) for k in current)
        rate = max(1.0, float(args.reset_home_max_speed))
        steps = max(1, int(math.ceil(max_delta / (rate * control_interval))))
        print(
            f"\n[HOME] {tag}把右臂送回起始姿态：最大关节偏差 {max_delta:.1f}，"
            f"限速 {rate:.0f}/s，约 {steps * control_interval:.1f}s",
            flush=True,
        )

        for step in range(1, steps + 1):
            alpha = step / steps
            # 每个控制周期都用**最新**观测重算，避免累积误差和跟不动
            obs = robot.get_observation()
            current = {k: float(v) for k, v in obs.items() if k in reset_home_target}
            if not current:
                break
            command = {k: current[k] + (reset_home_target[k] - current[k]) * alpha for k in current}
            robot.send_action(command)
            precise_sleep(control_interval)

        # 最后再顶一下目标姿态，确保到位（并让 host 保持在位）
        robot.send_action(dict(reset_home_target))
        precise_sleep(max(control_interval, 0.2))
        print("[HOME] 回位完成", flush=True)

    def reset_environment(next_episode: int) -> None:
        """Wait for manual scene reset while draining remote observations."""
        if args.reset_time == 0:
            return

        # 下面的倒计时是用 \r 原地刷新的，很容易让人以为「跑完一集就结束了」。
        # 所以先打一行明确的提示：这是**自动继续**，不是结束。
        print(
            f"\n[RESET] 第 {next_episode - 1}/{args.num_episodes} 集已保存到磁盘。"
            f"接下来自动开始第 {next_episode} 集 —— 不想继续就按 Ctrl-C（已跑完的集会保留）。",
            flush=True,
        )

        # 先把机械臂送回起始姿态（清空工作空间），再给倒计时让操作者摆物块。
        if reset_home_target:
            return_home(f"第 {next_episode} 集前 ")
            print(
                f"        右臂已回到起始姿态。现在把物块摆回原位，"
                f"{args.reset_time:.0f} 秒后自动开跑。",
                flush=True,
            )
        else:
            print(
                f"        请把物块摆回原位；{args.reset_time:.0f} 秒后自动开跑。",
                flush=True,
            )

        deadline = time.monotonic() + args.reset_time
        last_remaining = None
        while True:
            remaining = max(0, math.ceil(deadline - time.monotonic()))
            if remaining != last_remaining:
                print(
                    f"\r[RESET] Prepare evaluation episode {next_episode}/{args.num_episodes}: "
                    f"{remaining}s remaining",
                    end="",
                    flush=True,
                )
                last_remaining = remaining
            if remaining == 0:
                break

            # Keep consuming observations during reset so the next episode starts
            # from a fresh remote frame rather than one cached before the reset.
            robot.get_observation()
            sleep_t = min(control_interval, max(0.0, deadline - time.monotonic()))
            if sleep_t > 0:
                precise_sleep(sleep_t)
        print(flush=True)

    try:
        # 第一集没有 reset_environment 前置调用，所以这里单独回位一次。
        # （训练数据每一集都是从同一个起始姿态示教的，第一集也不例外。）
        if reset_home_target:
            return_home("第 1 集前 ")
        while recorded < args.num_episodes:
            log_say(f"Eval episode {recorded + 1} of {args.num_episodes}")
            stop_inference(engine)
            engine.reset()
            engine.start()
            interpolator.reset()
            engine.resume()
            start = time.perf_counter()
            restart_pending = False
            episode_frames = 0

            while (time.perf_counter() - start) < args.episode_time:
                loop_start = time.perf_counter()

                obs_raw = robot.get_observation()
                previous_context = safety_guard.context
                obs_raw, reason = safety_guard.check_observation(robot, obs_raw)
                if reason:
                    if not restart_pending:
                        logging.warning("%s；等待新反馈，暂不发送动作。", reason)
                        engine.pause()
                        interpolator.reset()
                        restart_pending = True
                    precise_sleep(control_interval)
                    continue
                if restart_pending or (
                    previous_context[0] is not None and safety_guard.context != previous_context
                ):
                    stop_inference(engine)
                    engine.reset()
                    interpolator.reset()
                    engine.start()
                    engine.resume()
                    restart_pending = False
                inference_context = safety_guard.context
                obs_processed = robot_observation_processor(obs_raw)
                if interpolator.needs_new_action():
                    engine.notify_observation(obs_processed)
                obs_frame = build_dataset_frame(dataset_features, obs_processed, prefix=OBS_STR)

                inference_started = time.perf_counter()
                if interpolator.needs_new_action():
                    action_tensor = engine.get_action(obs_frame)
                    if action_tensor is not None:
                        if action_tensor.ndim != 1 or action_tensor.numel() != len(ordered_action_keys):
                            raise ValueError("Policy action shape does not match the robot actuator schema")
                        interpolator.add(action_tensor.cpu())

                obs_raw, reason = safety_guard.check_observation(
                    robot, obs_raw,
                    refresh=time.perf_counter() - inference_started >= control_interval,
                )
                if reason or safety_guard.context != inference_context:
                    # Discard predictions across joint protection or prolonged feedback loss.
                    engine.pause()
                    interpolator.reset()
                    restart_pending = True
                    precise_sleep(control_interval)
                    continue
                obs_processed = robot_observation_processor(obs_raw)
                obs_frame = build_dataset_frame(dataset_features, obs_processed, prefix=OBS_STR)
                interp_action = interpolator.get()
                if interp_action is not None:
                    action_dict = {k: interp_action[i].item() for i, k in enumerate(ordered_action_keys)}
                    sent = robot.send_action(robot_action_processor((action_dict, obs_raw)))
                    if sent and robot.observation_updated:
                        action_frame = build_dataset_frame(dataset_features, action_dict, prefix=ACTION)
                        dataset.add_frame({**obs_frame, **action_frame, "task": args.task_description})
                        episode_frames += 1

                dt = time.perf_counter() - loop_start
                if (sleep_t := control_interval - dt) > 0:
                    precise_sleep(sleep_t)

            engine.pause()
            if episode_frames:
                dataset.save_episode()
                print(
                    f"\n[SAVED] 第 {recorded + 1}/{args.num_episodes} 集：{episode_frames} 帧"
                    f"（{episode_frames / args.fps:.1f}s）已写入 {dataset.root}",
                    flush=True,
                )
            else:
                logging.warning("No evaluation frames collected; skipping empty episode.")
            recorded += 1
            if recorded < args.num_episodes:
                reset_environment(recorded + 1)
    finally:
        engine.pause()
        try:
            robot.send_action(hold_action(robot.last_remote_state))
        finally:
            try:
                engine.stop()
            finally:
                try:
                    if dataset.has_pending_frames():
                        dataset.save_episode()
                finally:
                    try:
                        dataset.finalize()
                    finally:
                        robot.disconnect()
    log_say("Evaluation complete")
    if args.push_to_hub:
        dataset.push_to_hub()


if __name__ == "__main__":
    main()
