#!/usr/bin/env python3

import argparse
import logging
import math
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.processor import make_default_processors
from lerobot.robots.alohamini import AlohaMiniClient, AlohaMiniClientConfig
from lerobot.robots.alohamini.config_alohamini import (
    alohamini_cameras_config,
    filter_cameras,
    parse_camera_names,
    parse_enabled_parts,
)
from lerobot.teleoperators.bi_so_leader import BiSOLeader, BiSOLeaderConfig
from lerobot.teleoperators.keyboard import KeyboardTeleop, KeyboardTeleopConfig
from lerobot.teleoperators.so_leader import SOLeaderConfig
from lerobot.teleoperators.uni_so_leader import UniSOLeader, UniSOLeaderConfig
from lerobot.utils.constants import ACTION, HF_LEROBOT_HOME, OBS_STR
from lerobot.utils.feature_utils import hw_to_dataset_features
from lerobot.utils.keyboard_input import init_keyboard_listener
from lerobot.utils.utils import init_logging, log_say
from lerobot.utils.visualization_utils import init_visualization, shutdown_visualization

from record_utils import record_loop

# 两条主臂的固定 udev 别名（见 tests/yuntao/90-alohamini-leader.rules）。
LEADER_ARM_PORTS = {
    "left": "/dev/am_arm_leader_left",
    "right": "/dev/am_arm_leader_right",
}


@contextmanager
def native_stderr_as_debug():
    """Capture native encoder stderr and route it to DEBUG logging."""
    try:
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError):
        yield
        return

    sys.stderr.flush()
    saved_stderr_fd = os.dup(stderr_fd)
    try:
        with tempfile.TemporaryFile(mode="w+b") as captured_stderr:
            os.dup2(captured_stderr.fileno(), stderr_fd)
            try:
                yield
            finally:
                sys.stderr.flush()
                os.dup2(saved_stderr_fd, stderr_fd)
                captured_stderr.seek(0)
                native_output = captured_stderr.read().decode(errors="replace").strip()
                if native_output:
                    logging.debug("Native video encoder output:\n%s", native_output)
    finally:
        os.close(saved_stderr_fd)


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
    # Keep the interactive output focused on recording state. Errors are still shown.
    init_logging(console_level="ERROR")
    parser = argparse.ArgumentParser(description="Record episodes with bi-arm teleoperation")
    parser.add_argument("--dataset.repo_id", "--dataset", dest="dataset_repo_id", type=str, required=True,
                    help="Dataset repo_id, e.g. liyitenga/record_20250914225057")
    parser.add_argument("--dataset.root", "--root", dest="dataset_root", type=str, default=None,
                    help="Local dataset root. Defaults to $HF_LEROBOT_HOME/<dataset.repo_id>.")
    parser.add_argument("--dataset.num_episodes", "--num_episodes", dest="num_episodes",
                        type=int, default=1, help="Number of episodes to record")
    parser.add_argument("--dataset.fps", "--fps", dest="fps", type=int, default=30, help="Frames per second")
    parser.add_argument("--dataset.episode_time_s", "--episode_time", dest="episode_time",
                        type=int, default=60, help="Duration of each episode (seconds)")
    parser.add_argument("--dataset.reset_time_s", "--reset_time", dest="reset_time",
                        type=int, default=10, help="Reset duration between episodes (seconds)")
    parser.add_argument("--dataset.single_task", "--task_description", dest="task_description",
                        type=str, default="My task description4", help="Task description")
    parser.add_argument(
        "--robot.remote_ip",
        "--remote_ip",
        dest="remote_ip",
        type=str,
        default="127.0.0.1",
        help="Robot host IP",
    )
    parser.add_argument("--robot.id", "--robot_id", dest="robot_id", type=str, default="my_alohamini", help="Robot ID")
    parser.add_argument(
        "--robot.robot_model",
        "--robot_model",
        dest="robot_model",
        type=str,
        default="alohamini1",
        choices=["alohamini1", "alohamini2", "alohamini2pro"],
        help="AlohaMini model. Must match the --robot_model used on the Pi host side.",
    )
    parser.add_argument(
        "--teleop.id",
        "--leader_id",
        dest="leader_id",
        type=str,
        default="so101_leader_bi",
        help="Leader arm device ID",
    )
    parser.add_argument(
        "--teleop.arm_profile",
        "--arm_profile",
        dest="arm_profile",
        type=str,
        default="so-arm-5dof",
        choices=["so-arm-5dof", "am-leader-6dof"],
        help="Leader arm profile selector.",
    )
    parser.add_argument(
        "--teleop.arm",
        "--teleop_arm",
        dest="teleop_arm",
        type=str,
        default="bi",
        choices=["bi", "left", "right"],
        help=(
            "Which leader arm(s) to teleoperate: bi (default, both), left, or right. "
            "Use right/left together with --robot.parts=right_arm/left_arm for single-arm recording."
        ),
    )
    parser.add_argument(
        "--robot.parts",
        "--robot_parts",
        dest="robot_parts",
        type=str,
        default="all",
        help=(
            "AlohaMini parts exposed to the dataset/policy: all (default) or a comma-separated "
            "subset of left_arm,right_arm,base,lift. Use right_arm for single-arm (right) recording. "
            "Must match the Host's --parts."
        ),
    )
    parser.add_argument(
        "--robot.cameras",
        "--robot_cameras",
        dest="robot_cameras",
        type=str,
        default="all",
        help=(
            "Comma-separated camera names to record, default all. Must match the Host's --cameras, "
            "otherwise the disabled cameras are recorded as black images."
        ),
    )
    parser.add_argument("--resume", action="store_true", help="Resume recording on existing dataset")
    parser.add_argument(
        "--profile-timing",
        "--profile_timing",
        type=parse_bool,
        nargs="?",
        const=True,
        default=False,
        help="Print average per-stage recording-loop timings once per second (default: false).",
    )
    parser.add_argument(
        "--display-data",
        "--display_data",
        type=parse_bool,
        nargs="?",
        const=True,
        default=False,
        help="Display observations and actions in Rerun while recording (default: disabled).",
    )
    parser.add_argument(
        "--dataset.push_to_hub",
        "--push_to_hub",
        dest="push_to_hub",
        type=parse_bool,
        nargs="?",
        const=True,
        default=True,
        help="Whether to upload the dataset to Hugging Face Hub after recording.",
    )

    args = parser.parse_args()

    # === Robot and teleop config ===
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
    if args.teleop_arm == "bi":
        leader_arm = BiSOLeader(
            BiSOLeaderConfig(
                left_arm_config=SOLeaderConfig(
                    port=LEADER_ARM_PORTS["left"],
                    arm_profile=args.arm_profile,
                ),
                right_arm_config=SOLeaderConfig(
                    port=LEADER_ARM_PORTS["right"],
                    arm_profile=args.arm_profile,
                ),
                id=args.leader_id,
            )
        )
    else:
        # 单臂：同一个 --teleop.id 会派生出 `<id>_right` 的校准文件，
        # 与双臂模式下的右臂共用同一份校准，不用重标。
        leader_arm = UniSOLeader(
            UniSOLeaderConfig(
                arm_config=SOLeaderConfig(
                    port=LEADER_ARM_PORTS[args.teleop_arm],
                    arm_profile=args.arm_profile,
                ),
                side=args.teleop_arm,
                id=args.leader_id,
            )
        )
    keyboard_config = KeyboardTeleopConfig()

    robot = AlohaMiniClient(robot_config)
    keyboard = KeyboardTeleop(keyboard_config)

    # 主臂能给出的 key 必须被机器人暴露的部件覆盖，否则 send_action() 会整包拒绝，
    # 报错要早、要直白：--teleop.arm 与 --robot.parts 必须匹配。
    leader_keys = {f"arm_{key}" for key in leader_arm.action_features}
    uncovered = sorted(leader_keys - set(robot.action_features))
    if uncovered:
        raise ValueError(
            f"主臂会发送机器人未暴露的 key：{uncovered}。"
            f"当前 --robot.parts={enabled_parts}（对应 --teleop.arm={args.teleop_arm} 需要一致）。"
            "单臂录制请用 --teleop.arm right --robot.parts right_arm。"
        )

    teleop_action_processor, robot_action_processor, robot_observation_processor = make_default_processors()

    # === Dataset setup ===
    action_features = hw_to_dataset_features(robot.action_features, ACTION)
    obs_features = hw_to_dataset_features(robot.observation_features, OBS_STR)
    dataset_features = {**action_features, **obs_features}
    dataset_root = Path(args.dataset_root) if args.dataset_root else HF_LEROBOT_HOME / args.dataset_repo_id

    if args.resume:
        dataset = LeRobotDataset.resume(
            repo_id=args.dataset_repo_id,
            root=dataset_root,
            image_writer_threads=4,
        )
    else:
        dataset = LeRobotDataset.create(
            repo_id=args.dataset_repo_id,
            root=dataset_root,
            fps=args.fps,
            features=dataset_features,
            robot_type=robot.name,
            use_videos=True,
            image_writer_threads=4,
        )

    # === Connect devices ===
    robot.connect()
    leader_arm.connect()
    keyboard.connect()

    listener, events = init_keyboard_listener()

    if not robot.is_connected or not leader_arm.is_connected or not keyboard.is_connected:
        raise ValueError("Robot or teleop is not connected!")
    print(
        f"Recording mode: parts={list(robot_config.enabled_parts)} "
        f"leader={args.teleop_arm} cameras={sorted(robot_config.cameras)} "
        f"action_dims={len(robot.action_features)}",
        flush=True,
    )
    if args.display_data:
        init_visualization("rerun", session_name="alohamini_record")
    recorded_episodes = 0

    def print_countdown(
        phase: str,
        episode_number: int,
        remaining_s: int,
        *,
        is_recording: bool,
        actual_fps: float | None = None,
    ) -> None:
        """Render every countdown with the same one-line layout."""
        recording_state = "RECORDING" if is_recording else "NOT RECORDING"
        # Keep this deliberately short: a wrapped terminal row cannot be reliably
        # rewritten with carriage return and would make every tick appear as a new line.
        message = f"[{phase:<7}] Ep {episode_number:<3} | {remaining_s:>4}s | {recording_state}"
        if actual_fps is not None:
            message += f" | FPS {actual_fps:>5.1f}/{args.fps}"
        # Clear and rewrite one terminal row so per-second updates do not scroll the screen.
        sys.stdout.write(f"\r\033[2K{message}")
        sys.stdout.flush()

    def episode_frame_count() -> int:
        """Return the number of frames currently buffered for the active episode."""
        episode_buffer = dataset.writer.episode_buffer
        return 0 if episode_buffer is None else int(episode_buffer["size"])

    def print_record_timing(timing: dict[str, float]) -> None:
        """Print one aggregated timing line without mixing it into the countdown row."""
        sys.stdout.write("\r\033[2K")
        print(
            f"[PC TIMING avg ms/frame] FPS={timing['capture_fps']:.1f}/{args.fps} "
            f"obs={timing['observation']:.1f} obs_proc={timing['observation_processing']:.1f} "
            f"frame={timing['frame_build']:.1f} teleop={timing['teleop']:.1f} "
            f"send={timing['send_action']:.1f} dataset={timing['dataset_write']:.1f} "
            f"display={timing['display']:.1f} sleep={timing['sleep']:.1f} "
            f"loop={timing['loop']:.1f}",
            flush=True,
        )
        decode_text = " ".join(
            f"{name}={value:.1f}"
            for name, value in timing.items()
            if name.startswith("decode_")
        )
        if "obs_wait" in timing:
            print(
                f"[PC OBS avg ms/frame] wait={timing['obs_wait']:.1f} "
                f"json={timing.get('obs_json', 0.0):.1f} {decode_text} "
                f"parse_decode={timing.get('obs_parse_decode', 0.0):.1f} "
                f"state={timing.get('obs_state', 0.0):.1f} "
                f"client_total={timing.get('obs_client_total', 0.0):.1f}",
                flush=True,
            )

    def reset_environment(episode_number: int) -> None:
        """Reset the scene while continuously draining remote observations."""
        countdown_stopped = threading.Event()

        def show_countdown() -> None:
            deadline = time.monotonic() + args.reset_time
            last_remaining = None
            while not countdown_stopped.is_set():
                remaining = max(0, math.ceil(deadline - time.monotonic()))
                if remaining != last_remaining:
                    print_countdown(
                        "RESET",
                        episode_number,
                        remaining,
                        is_recording=False,
                    )
                    last_remaining = remaining
                if remaining == 0:
                    break
                countdown_stopped.wait(0.1)

        countdown_thread = threading.Thread(target=show_countdown, daemon=True)
        countdown_thread.start()
        try:
            record_loop(
                robot=robot,
                events=events,
                fps=args.fps,
                leader_arm=leader_arm,
                keyboard=keyboard,
                control_time_s=args.reset_time,
                single_task=args.task_description,
                teleop_action_processor=teleop_action_processor,
                robot_action_processor=robot_action_processor,
                robot_observation_processor=robot_observation_processor,
                display_data=args.display_data,
            )
        finally:
            countdown_stopped.set()
            countdown_thread.join()
            print(flush=True)

    def wait_for_fresh_observation(episode_number: int) -> None:
        """Do not start an episode with an observation cached during reset or save."""
        previous_sequence = robot.observation_sequence
        deadline = time.monotonic() + robot.config.connect_timeout_s
        while robot.observation_sequence == previous_sequence:
            robot.get_observation()
            if robot.observation_sequence == previous_sequence and time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Episode {episode_number} did not receive a fresh observation before recording. "
                    "Recording was stopped to avoid saving cached reset frames."
                )

    def record_episode(episode_number: int) -> None:
        """Record one episode while displaying its remaining time."""
        countdown_stopped = threading.Event()
        initial_frame_count = episode_frame_count()
        recording_started_at = time.monotonic()

        def show_countdown() -> None:
            deadline = time.monotonic() + args.episode_time
            last_remaining = None
            last_sample_t = recording_started_at
            last_frame_count = initial_frame_count
            actual_fps = None
            while not countdown_stopped.is_set():
                now = time.monotonic()
                remaining = max(0, math.ceil(deadline - now))
                if remaining != last_remaining:
                    current_frame_count = episode_frame_count()
                    sample_duration_s = now - last_sample_t
                    if sample_duration_s >= 0.5:
                        actual_fps = (current_frame_count - last_frame_count) / sample_duration_s
                        last_sample_t = now
                        last_frame_count = current_frame_count
                    print_countdown(
                        "RECORD",
                        episode_number,
                        remaining,
                        is_recording=True,
                        actual_fps=actual_fps,
                    )
                    last_remaining = remaining
                if remaining == 0:
                    break
                countdown_stopped.wait(0.1)

        countdown_thread = threading.Thread(target=show_countdown, daemon=True)
        countdown_thread.start()
        try:
            record_loop(
                robot=robot,
                events=events,
                fps=args.fps,
                dataset=dataset,
                leader_arm=leader_arm,
                keyboard=keyboard,
                control_time_s=args.episode_time,
                single_task=args.task_description,
                teleop_action_processor=teleop_action_processor,
                robot_action_processor=robot_action_processor,
                robot_observation_processor=robot_observation_processor,
                timing_callback=print_record_timing if args.profile_timing else None,
                display_data=args.display_data,
            )
        finally:
            recording_elapsed_s = time.monotonic() - recording_started_at
            recorded_frames = episode_frame_count() - initial_frame_count
            countdown_stopped.set()
            countdown_thread.join()
            print(flush=True)
            average_fps = recorded_frames / recording_elapsed_s if recording_elapsed_s > 0 else 0.0
            print(
                f"Episode {episode_number} capture rate: {average_fps:.2f} FPS "
                f"({recorded_frames} frames in {recording_elapsed_s:.2f}s; target {args.fps} FPS)",
                flush=True,
            )

    while recorded_episodes < args.num_episodes and not events["stop_recording"]:
        episode_number = dataset.num_episodes + 1
        remaining_episodes = args.num_episodes - recorded_episodes
        wait_for_fresh_observation(episode_number)
        if events["stop_recording"]:
            break
        log_say(f"Recording episode {episode_number}")
        print(
            f"Episode {episode_number} recording started. "
            f"{remaining_episodes} episode(s) remaining. Press -> to end recording; "
            "press R to discard and re-record.",
            flush=True,
        )

        # === Main record loop ===
        record_episode(episode_number)

        log_say(f"Recording episode {episode_number} ended")
        print(f"Episode {episode_number} recording ended. Resetting before save.", flush=True)

        # Finish resetting first. No dataset frames are written during this phase.
        if not events["stop_recording"]:
            events["exit_early"] = False
            reset_environment(episode_number)

        if events["rerecord_episode"]:
            print(f"Discarding episode {episode_number}; it will not be saved.", flush=True)
            events["rerecord_episode"] = False
            events["exit_early"] = False
            dataset.clear_episode_buffer()
            continue

        print(
            f"Reset finished. Saving episode {episode_number}; this may take a while. Please wait...",
            flush=True,
        )
        save_started_at = time.perf_counter()
        with native_stderr_as_debug():
            dataset.save_episode()
        print(
            f"Episode {episode_number} saved in {time.perf_counter() - save_started_at:.1f} second(s).",
            flush=True,
        )
        recorded_episodes += 1
        events["rerecord_episode"] = False
        events["exit_early"] = False

    # === Clean up ===
    robot.disconnect()
    leader_arm.disconnect()
    keyboard.disconnect()
    if listener is not None:
        listener.stop()
    print("Saving dataset...", flush=True)
    with native_stderr_as_debug():
        dataset.finalize()
    if args.push_to_hub:
        dataset.push_to_hub()
    if args.display_data:
        shutdown_visualization("rerun")
    print(f"Dataset saved at {dataset.root.resolve()}", flush=True)


if __name__ == "__main__":
    main()
