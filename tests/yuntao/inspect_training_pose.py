#!/usr/bin/env python3
"""从训练数据集里算出「示教起始姿态」的统计量。

pi0.5 把 observation.state 离散化后写进提示词；一旦起始姿态超出训练分布，
离散化会饱和到 255/0 边界 bin，模型看到的是没见过的输入。
所以上机前必须知道示教是从哪个姿态开始的。

用法（服务器上，lerobot_alohamini 环境）：
    python tests/yuntao/inspect_training_pose.py
    DATASET_ROOT=/path/to/other_dataset python tests/yuntao/inspect_training_pose.py
"""
from __future__ import annotations

import glob
import json
import os

import numpy as np
import pandas as pd

D = os.environ.get(
    "DATASET_ROOT",
    "/data/home/yuntao/MyProject/2_Alohamini2/datasets/am2_right_arm_test",
)

files = sorted(glob.glob(D + "/data/**/*.parquet", recursive=True))
print(f"parquet files: {len(files)}")

info = json.load(open(D + "/meta/info.json"))
names = info["features"]["observation.state"].get("names")
print(f"state names: {names}")
print(f"episodes={info.get('total_episodes')} frames={info.get('total_frames')} fps={info.get('fps')}")

starts, all_frames = [], []
for f in files:
    df = pd.read_parquet(f, columns=["episode_index", "frame_index", "observation.state"])
    all_frames.append(np.stack(df["observation.state"].to_numpy()))
    first = df[df.frame_index == 0].sort_values("episode_index")
    starts.append(np.stack(first["observation.state"].to_numpy()))

S = np.concatenate(starts)  # (n_episodes, 7)
A = np.concatenate(all_frames)
print(f"每 episode 起始帧数: {len(S)}")

hdr = f"{'joint':<32}{'start_mean':>11}{'start_std':>10}{'start_min':>10}{'start_max':>10}"
print("\n=== 每条 episode 的**起始帧**姿态（上机前把右臂摆到这里）")
print(hdr)
print("-" * len(hdr))
for i in range(7):
    nm = names[i] if names else str(i)
    print(f"{nm:<32}{S[:, i].mean():11.3f}{S[:, i].std():10.3f}{S[:, i].min():10.3f}{S[:, i].max():10.3f}")

q01 = np.quantile(A, 0.01, axis=0)
q99 = np.quantile(A, 0.99, axis=0)
hdr2 = f"{'joint':<32}{'train_q01':>11}{'train_q99':>11}"
print("\n=== 全部帧 q01/q99（= checkpoint 里存的训练分布）")
print(hdr2)
print("-" * len(hdr2))
for i in range(7):
    nm = names[i] if names else str(i)
    print(f"{nm:<32}{q01[i]:11.3f}{q99[i]:11.3f}")

mean_start = S.mean(axis=0)
print("\n=== 建议起始姿态（起始帧均值）")
print('  --state="' + ", ".join(f"{v:.3f}" for v in mean_start) + '"')

print("\n=== 每条 episode 起始帧 vs 训练 q01/q99 的越界情况")
for i in range(7):
    nm = names[i] if names else str(i)
    bad = (S[:, i] < q01[i]) | (S[:, i] > q99[i])
    if bad.any():
        print(f"  {nm}: {int(bad.sum())}/{len(S)} 条起始帧超出 q01~q99")
print("  （若上面没有输出，说明所有示教起始姿态都落在训练分布内）")
