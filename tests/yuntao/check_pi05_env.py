#!/usr/bin/env python
"""Fast health check for pi05 training on this box.

Reproduces the two startup steps that used to fail, WITHOUT loading the 14.5 GB
checkpoint, so it finishes in a few seconds instead of ~20 minutes.

Run it any time before a long training run:

    conda activate lerobot_alohamini
    export HF_HUB_OFFLINE=1          # optional: proves it works with zero network
    python check_pi05_env.py

Expected output ends with:  ALL CHECKS PASSED
"""

import os
import time

print("1/2 loading PaliGemma tokenizer (used to die with GatedRepoError 403)...")
t0 = time.time()
from transformers import AutoTokenizer

tok = AutoTokenizer.from_pretrained("google/paligemma-3b-pt-224")
print(f"    OK in {time.time() - t0:.1f}s: {type(tok).__name__}, vocab={tok.vocab_size}")

prompt = "Task: pick up the red cube, State: " + " ".join(["128"] * 6) + ";\nAction: "
ids = tok(prompt, padding="max_length", truncation=True, max_length=200)["input_ids"]
real = [i for i in ids if i != tok.pad_token_id]
print(f"    prompt tokenizes to {len(real)} real tokens; decode={tok.decode(real)!r}")

print("2/2 building the pi05 preprocessor pipeline (used to die here too)...")
t0 = time.time()
from lerobot.policies.factory import make_pre_post_processors
from lerobot.policies.pi05.configuration_pi05 import PI05Config

pre, post = make_pre_post_processors(policy_cfg=PI05Config(), pretrained_path="lerobot/pi05_base")
names = [type(s).__name__ for s in pre.steps]
assert any("Tokenizer" in n for n in names), names
print(f"    OK in {time.time() - t0:.1f}s: {names}")

print(f"\nHF_HUB_OFFLINE={os.environ.get('HF_HUB_OFFLINE', 'unset')}, "
      f"no_proxy={os.environ.get('no_proxy', 'unset')}")
print("ALL CHECKS PASSED")
