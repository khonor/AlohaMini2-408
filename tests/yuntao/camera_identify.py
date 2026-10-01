#!/usr/bin/env python3
"""找出所有真实 USB 相机，各拍一张照片，方便肉眼确认「哪个是哪个」。

## 为什么需要这个脚本

本机的相机模块型号全是同一个 `Integrated_Webcam_HD`，而且**没有序列号**，
所以 udev 规则不能用 `ATTRS{serial}`，只能用 **`ID_PATH`**（物理 USB 拓扑路径）。
`ID_PATH` 本身没有可读含义 —— 想知道 `platform-xhci-hcd.0-usb-0:1:1.0`
到底是「车前摄像头」还是「右腕摄像头」，只能拍张照看一眼。

另外树莓派上还有一堆**假的** video 节点（ISP 编解码器，`platform-...-pisp_be` /
`codec`），它们也能被 `cv2.VideoCapture` 打开但永远读不到画面。本脚本用
`ID_V4L_CAPABILITIES` 里有没有 `capture` 来把它们过滤掉。

## 用法

    python tests/yuntao/camera_identify.py
    python tests/yuntao/camera_identify.py --frames 30 --warmup 3

照片存到 `tests/yuntao/camera_snapshots/`，文件名就是可读的 ID_PATH，
同时会在该目录生成 `mapping.txt`。

在远程 SSH 里怎么看图：

    scp -r lab408@10.42.0.186:~/MyProject/lerobot_alohamini/tests/yuntao/camera_snapshots ./
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "camera_snapshots"


def udev_props(dev: str) -> dict[str, str]:
    try:
        out = subprocess.run(
            ["udevadm", "info", "-q", "property", "-n", dev],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:
        return {}
    props: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k] = v
    return props


def find_real_cameras() -> list[dict[str, str]]:
    """返回真实的 capture 设备（按 video 编号排序）。

    用 ID_V4L_CAPABILITIES 过滤掉 ISP 编解码器等假节点 —— 它们虽然也能被
    OpenCV 打开，但永远没有画面。
    """
    cams: list[dict[str, str]] = []
    for dev in sorted(Path("/dev").glob("video*"), key=lambda p: int(p.name[5:] or -1)):
        props = udev_props(str(dev))
        if not props:
            continue
        # 真实 USB 摄像头；平台节点（pisp_be / codec）直接跳过
        if not props.get("ID_PATH", "").startswith("platform-xhci"):
            continue
        # video1/3/5 是同一条 USB 通路上的 metadata 节点，没有 capture 能力
        if "capture" not in props.get("ID_V4L_CAPABILITIES", ""):
            continue
        cams.append(
            {
                "dev": str(dev),
                "index": dev.name[5:],
                "id_path": props.get("ID_PATH", "unknown"),
                "model": props.get("ID_MODEL", "unknown"),
                "usb_path": props.get("ID_PATH_SERIAL", "") or props.get("DEVPATH", ""),
            }
        )
    return cams


def sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:+-]", "_", name)


def main() -> None:
    p = argparse.ArgumentParser(description="给每个真实相机拍一张照片用于人工辨认")
    p.add_argument("--frames", type=int, default=20, help="每个相机连读多少帧后取最后一帧")
    p.add_argument("--warmup", type=float, default=2.0, help="开始读之前等多少秒（自动曝光）")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    args = p.parse_args()

    try:
        import cv2
    except ImportError:
        print("缺少 opencv，请先：pip install opencv-python", file=sys.stderr)
        sys.exit(1)

    import time

    import numpy as np

    cams = find_real_cameras()
    if not cams:
        print("没有找到真实 USB 相机。检查摄像头是否插好。")
        sys.exit(1)

    print(f"找到 {len(cams)} 个真实相机：\n")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = []

    for c in cams:
        print(f"--- {c['dev']}  ({c['model']})")
        print(f"    ID_PATH = {c['id_path']}")

        cap = cv2.VideoCapture(int(c["index"]), cv2.CAP_V4L2)
        if not cap.isOpened():
            print("    ❌ 打不开\n")
            lines.append(f"{c['dev']}\t{c['id_path']}\tOPEN_FAILED")
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        time.sleep(args.warmup)

        frame = None
        for _ in range(args.frames):
            ok, f = cap.read()
            if ok and f is not None:
                frame = f
        cap.release()

        if frame is None:
            print("    ❌ 读不到画面（可能镜头盖没摘 / 是假节点）\n")
            lines.append(f"{c['dev']}\t{c['id_path']}\tNO_FRAME")
            continue

        mean = float(np.mean(frame))
        std = float(np.std(frame))
        out = OUT_DIR / f"{sanitize(c['id_path'])}.png"
        cv2.imwrite(str(out), frame)
        verdict = "全黑（镜头盖/没曝光）" if mean < 3 else "有画面"
        print(f"    亮度均值={mean:6.1f} 标准差={std:6.1f}  -> {verdict}")
        print(f"    已保存 {out.name}\n")
        lines.append(f"{c['dev']}\t{c['id_path']}\t{out.name}\tmean={mean:.1f}\tstd={std:.1f}\t{verdict}")

    (OUT_DIR / "mapping.txt").write_text(
        "# dev\tID_PATH\t快照\t亮度\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )

    print(f"照片与 mapping.txt 都在：{OUT_DIR}")
    print("在远程 SSH 里取回来看：")
    print(f"    scp -r lab408@<Pi_IP>:{OUT_DIR} ./")


if __name__ == "__main__":
    main()
