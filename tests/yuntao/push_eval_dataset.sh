#!/bin/bash
# 把已经跑完的评估数据集上传到 Hugging Face Hub（**服务器侧**执行）。
#
# 用法：
#   bash tests/yuntao/push_eval_dataset.sh                          # 传默认那个
#   bash tests/yuntao/push_eval_dataset.sh khonor/eval_pi05_run2    # 传指定 repo_id
#   PRIVATE=false bash tests/yuntao/push_eval_dataset.sh            # 传成公开的
#
# 为什么需要单独一个脚本：
#   run_pi05_eval.sh 里为了不被网络卡住，强制了 HF_HUB_OFFLINE=1 并清掉了代理。
#   上传必须反过来：要有代理、不能离线、要静默关闭离线模式。
#   而且 evaluate_bi.py 的 push_to_hub() 在 try/finally 之外 ——
#   用 Ctrl-C 中止时它不会执行，所以「已经跑好的」数据只能靠本脚本补传。
set -euo pipefail

REPO_ID="${1:-khonor/eval_pi05_am2_right_arm}"
ROOT="${2:-$HOME/.cache/huggingface/lerobot/$REPO_ID}"
PRIVATE="${PRIVATE:-true}"
PY="${PY:-/data/home/yuntao/miniconda3/envs/lerobot_alohamini/bin/python}"

# 代理：HF 在这台机器上必须走操作端 PC 的代理（实测直连不通）。
# 换代理就改这里，或临时 `PROXY=http://1.2.3.4:7897 bash ...`。
PROXY="${PROXY:-http://10.23.15.171:7897}"
export http_proxy="$PROXY" https_proxy="$PROXY"
export no_proxy="localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
export NO_PROXY="$no_proxy"
# 关键：必须解除离线模式，否则 huggingface_hub 拒绝任何网络操作
unset HF_HUB_OFFLINE HF_HUB_DISABLE_TELEMETRY 2>/dev/null || true

echo "=== 上传目标"
echo "  repo_id : $REPO_ID"
echo "  local   : $ROOT"
echo "  private : $PRIVATE"
echo "  proxy   : $PROXY"
echo

if [ ! -d "$ROOT" ]; then
  echo "!! 本地目录不存在：$ROOT"
  echo "   先确认 EVAL_NAME；默认在 ~/.cache/huggingface/lerobot/<HF_USER>/<EVAL_NAME>"
  exit 1
fi
if [ ! -f "$ROOT/meta/info.json" ]; then
  echo "!! $ROOT 不像一个 LeRobot 数据集（缺少 meta/info.json）"
  exit 1
fi

# 先确认 token 有效、且有写权限
echo "=== 检查 Hugging Face 身份"
"$PY" - <<'PYEOF' || exit 1
import sys
from huggingface_hub import HfApi
try:
    who = HfApi().whoami()
except Exception as e:
    print(f"!! 未登录或 token 无效：{type(e).__name__}: {e}")
    print("   先登录：hf auth login   （或 export HF_TOKEN=hf_xxx）")
    sys.exit(1)
print(f"  [OK] 已登录：{who.get('name')}")
PYEOF
echo

echo "=== 统计本地内容"
DATA_SUMMARY=$("$PY" - "$ROOT" <<'PYEOF'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
info = json.loads((root / "meta/info.json").read_text())
print(f"  episodes={info.get('total_episodes')} frames={info.get('total_frames')} "
      f"fps={info.get('fps')} codebase={info.get('codebase_version')}")
PYEOF
)
echo "$DATA_SUMMARY"
echo

echo "=== 开始上传（大文件走 LFS，可能几分钟）"
"$PY" - "$REPO_ID" "$ROOT" "$PRIVATE" <<'PYEOF'
import sys
from lerobot.datasets.lerobot_dataset import LeRobotDataset

repo_id, root, private = sys.argv[1], sys.argv[2], sys.argv[3].lower() == "true"

# 只读本地，不要从 Hub 同步（force_cache_sync=False，且本地已有 meta）
ds = LeRobotDataset(repo_id, root=root)
print(f"  打开成功：{ds.meta.total_episodes} 集 / {ds.meta.total_frames} 帧")
ds.push_to_hub(
    private=private,
    tags=["lerobot", "alohamini", "pi05", "evaluation"],
    license="apache-2.0",
)
print(f"\n[OK] 已上传：https://huggingface.co/datasets/{repo_id}")
PYEOF
