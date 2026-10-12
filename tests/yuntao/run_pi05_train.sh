#!/bin/bash
# pi05 fine-tune on khonor/am2_right_arm_test — single RTX 3090 Ti (24 GiB), CUDA_VISIBLE_DEVICES=0
#
# Two independent problems are fixed here; both are REQUIRED.
#
# (1) GATED REPO / 403.  google/paligemma-3b-pt-224 is a gated model and this account is
#     not in its authorized list. Transformers 5.x *unconditionally* probes
#     `chat_template.jinja`, a file that does not exist in that repo at all (verified via the
#     public tree API). On a gated repo HF answers 403 for every path instead of 404, so
#     transformers raises GatedRepoError and dies in make_pre_post_processors -> tokenizer_processor.
#     All PaliGemma tokenizer files were already in the local HF cache; the only missing piece is
#     that known-absent file, so we record it with the Hub's own "no-exist" marker (the same thing
#     huggingface_hub writes on a 404).  This makes the tokenizer resolve 100% from cache:
#         ~/.cache/huggingface/hub/models--google--paligemma-3b-pt-224/.no_exist/<commit>/chat_template.jinja
#     Fallback if the network ever misbehaves: export HF_HUB_OFFLINE=1 and set push_to_hub=false.
#
# (2) no_proxy MUST NOT contain "[::1]".  httpx cannot parse that URL pattern and crashes with
#     httpx.InvalidURL before making any request.  We rewrite it cleanly below.
#
# MEMORY: the checkpoint is 3.62 B params, all fp32 (14.5 GB). Full fine-tuning needs
#     params 6.7 + grads 6.7 + fp32 AdamW m,v 27.0 = ~40.5 GiB of VRAM -> impossible on 24 GiB.
#     train_expert_only=true freezes the 2.93 B PaliGemma VLM and trains only the 0.69 B action
#     expert: 13.2 GiB + activations -> fits with room to spare.
set -euo pipefail

export CUDA_VISIBLE_DEVICES=0
export http_proxy=http://10.23.15.171:7897
export https_proxy=http://10.23.15.171:7897
export no_proxy="localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
export NO_PROXY="$no_proxy"
export PYTHONUNBUFFERED=1
export TOKENIZERS_PARALLELISM=false

PROJ=/data/home/yuntao/MyProject/2_Alohamini2
OUT="$PROJ/lerobot_alohamini/outputs/train/pi05_am2_right_arm"

# Self-heal the "this file does not exist upstream" marker (idempotent, no network).
# Needed because the gated repo answers 403 for a path that would normally 404.
HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub/models--google--paligemma-3b-pt-224"
if [ -f "$HUB/refs/main" ]; then
  COMMIT=$(cat "$HUB/refs/main")
  mkdir -p "$HUB/.no_exist/$COMMIT"
  touch "$HUB/.no_exist/$COMMIT/chat_template.jinja"
  echo "gated-repo workaround active: $HUB/.no_exist/$COMMIT/chat_template.jinja"
fi

lerobot-train \
  --dataset.repo_id=khonor/am2_right_arm_test \
  --dataset.root="$PROJ/datasets/am2_right_arm_test" \
  --dataset.video_backend=pyav \
  --rename_map='{"observation.images.forward":"observation.images.base_0_rgb","observation.images.wrist_right":"observation.images.right_wrist_0_rgb"}' \
  --policy.path=lerobot/pi05_base \
  --policy.device=cuda \
  --policy.dtype=bfloat16 \
  --policy.gradient_checkpointing=true \
  --policy.train_expert_only=true \
  --policy.use_relative_actions=true \
  --batch_size=2 \
  --num_workers=2 \
  --steps=40000 \
  --save_freq=10000 \
  --log_freq=50 \
  --policy.scheduler_warmup_steps=500 \
  --policy.scheduler_decay_steps=40000 \
  --output_dir="$OUT" \
  --job_name=pi05_am2_right_arm \
  --policy.repo_id=khonor/pi05_am2_right_arm \
  --wandb.enable=false
