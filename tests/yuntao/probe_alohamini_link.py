#!/usr/bin/env python3
"""AlohaMini 链路探针 —— **只读，不发任何动作**。

背景：这台服务器（10.23.12.237）到树莓派（10.42.0.186）没有路由，
只有你的 aaa 主机（10.44.242.239）能到树莓派，而且方向是单向的
（aaa 能 SSH 到服务器，服务器到 aaa 不通）。所以必须在 aaa 上开反向隧道：

    # 在 aaa 上执行，保持这个终端开着
    ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \\
        -R 5555:10.42.0.186:5555 \\
        -R 5556:10.42.0.186:5556 \\
        yuntao@10.23.12.237

  `-R` 的监听默认绑在服务器的 **loopback** 上，所以服务器侧用 127.0.0.1 访问。
  端口故意用 5555/5556 原样映射 —— evaluate_bi.py 没有暴露 --robot.port_zmq_* 参数，
  只有这样它才能不改代码直接用。

然后在服务器上跑本脚本来验证链路：

    python tests/yuntao/probe_alohamini_link.py

⚠️ 本脚本只做 connect() + get_observation()，**绝不调用 send_action()**。
   按 host 的规则「仅观察状态的客户端不会获取控制权」，所以机械臂不会动、
   也不会抢占控制权，可以随时安全地跑。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import lerobot.robots.alohamini  # noqa: F401 — 注册 alohamini / alohamini_client
from lerobot.robots.alohamini import AlohaMiniClient, AlohaMiniClientConfig
from lerobot.robots.alohamini.config_alohamini import (
    alohamini_cameras_config,
    filter_cameras,
    parse_camera_names,
    parse_enabled_parts,
)

OK = "  [OK]  "
BAD = "  [!!]  "


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AlohaMini 链路探针（只读，不动机械臂）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--remote-ip", default="127.0.0.1",
                        help="隧道场景填 127.0.0.1；直连树莓派时填 10.42.0.186")
    parser.add_argument("--robot-model", default="alohamini2",
                        choices=["alohamini1", "alohamini2", "alohamini2pro"],
                        help="必须与树莓派 host 端 --robot_model 一致")
    parser.add_argument("--parts", default="right_arm",
                        help="必须与 host 端 --parts 一致（子集也可）")
    parser.add_argument("--cameras", default="forward,wrist_right",
                        help="必须与 host 端 --cameras 一致")
    parser.add_argument("--samples", type=int, default=30, help="采样多少帧测 RTT")
    args = parser.parse_args()

    enabled_parts = parse_enabled_parts(args.parts)
    cameras, unknown = filter_cameras(
        alohamini_cameras_config(), parse_camera_names(args.cameras)
    )
    if unknown:
        parser.error(f"未知相机名 {unknown}；可用：{sorted(alohamini_cameras_config())}")

    cfg = AlohaMiniClientConfig(
        remote_ip=args.remote_ip,
        id="link_probe",
        robot_model=args.robot_model,
        enabled_parts=enabled_parts,
        cameras=cameras,
    )
    robot = AlohaMiniClient(cfg)

    print(f"\n=== 连接 {args.remote_ip} (model={args.robot_model}, parts={enabled_parts}, "
          f"cameras={sorted(cameras)})")
    t0 = time.perf_counter()
    try:
        robot.connect()
    except Exception as e:  # noqa: BLE001 — 探针要把失败原因原样打出来
        print(BAD + f"连接失败：{type(e).__name__}: {e}")
        print("\n  排查顺序：")
        print("    1) aaa 上的隧道还在吗？（那个 ssh -N -R 终端有没有断）")
        print("    2) 树莓派上 alohamini_host 起了吗？")
        print(f"    3) 在 aaa 上直接验证树莓派：ping 10.42.0.186")
        print("    4) 服务器上端口有没有被监听：ss -ltn | grep -E '5555|5556'")
        return 1
    print(OK + f"握手成功，耗时 {time.perf_counter() - t0:.2f}s")

    # host 端在握手时广播的元数据（model / parts 不一致时 connect 就已经报错了）
    for attr in ("robot_type", "name"):
        if hasattr(robot, attr):
            print(f"    {attr} = {getattr(robot, attr)}")
    if getattr(robot, "latest_safety_status", None):
        print(f"    安全状态: {robot.latest_safety_status}")

    print(f"\n=== 观测特征（应与训练时的 7 维右臂 + 2 路相机一致）")
    obs_feats = robot.observation_features
    for k, v in obs_feats.items():
        print(f"    {k:<40} {v}")
    act_feats = robot.action_features
    print(f"    action 维度: {len(act_feats)} -> {list(act_feats)}")

    print(f"\n=== 连续取 {args.samples} 帧（测 RTT；host 的反馈时限是 250ms）")
    rtts: list[float] = []
    last_obs = None
    for i in range(args.samples):
        t = time.perf_counter()
        obs = robot.get_observation()
        dt = (time.perf_counter() - t) * 1000.0
        rtts.append(dt)
        last_obs = obs
        if i == 0:
            print(f"    第 1 帧键: {sorted(obs.keys())}")
    print(f"    RTT  min/中位/p95/max = {min(rtts):.1f} / {statistics.median(rtts):.1f} / "
          f"{sorted(rtts)[int(len(rtts) * 0.95)]:.1f} / {max(rtts):.1f} ms")

    failures = 0
    if statistics.median(rtts) > 250:
        print(BAD + "中位 RTT 超过 250ms —— 同步推理的反馈时限会不满足，评估会频繁暂停")
        failures += 1
    elif max(rtts) > 250:
        print(f"  [~~]  最慢一帧 {max(rtts):.1f}ms 超过 250ms，偶尔暂停属正常，留意即可")
    else:
        print(OK + "RTT 全程在 250ms 反馈时限内")

    # 相机帧是不是真的到了（host 没开相机会给占位/全黑图）
    if last_obs:
        for cam in cameras:
            if cam in last_obs:
                img = last_obs[cam]
                print(f"    {cam:<14} shape={getattr(img, 'shape', None)} "
                      f"dtype={getattr(img, 'dtype', None)} "
                      f"非零像素占比={float((img != 0).mean()):.1%}")
            else:
                print(BAD + f"观测里没有相机 {cam}")
                failures += 1

        # 直接给成 --state=... 能用的格式
        joints = [k for k in last_obs if k.endswith(".pos")]
        if joints:
            print("\n=== 当前关节角（可直接喂给 verify_pi05_inference.py --state=）")
            vals = ", ".join(f"{last_obs[k]:.3f}" for k in joints)
            print(f'    --state="{vals}"')
            print("    用它可以先确认起始姿态是否落在训练分布内（超出会被离散化饱和）。")

    robot.disconnect()
    print("\n" + "=" * 62)
    if failures:
        print(f"链路可用，但有 {failures} 项需要注意（见上）。")
        return 1
    print("链路全部正常 ✅ 可以跑 run_pi05_eval.sh 了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
