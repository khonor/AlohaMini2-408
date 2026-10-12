#!/bin/bash
# Resumable download of lerobot/pi05_base model.safetensors (14,467,165,872 bytes).
# huggingface_hub 1.x is NOT resumable (fresh uuid .incomplete per process), so we
# resume the LFS file directly with curl -C - and install it into the HF cache after
# verifying its sha256.
set -u

export http_proxy=http://10.23.15.171:7897
export https_proxy=http://10.23.15.171:7897
export no_proxy="localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
export NO_PROXY="$no_proxy"

TOTAL=14467165872
SHA=0eb11ca9587678c1d2ef8cf32807c29f8ce53a2bfdfc1aa4a4c96f16fca59b0f
OUT=/data/home/yuntao/MyProject/2_Alohamini2/pi05_base.model.safetensors.part
URL=https://huggingface.co/lerobot/pi05_base/resolve/main/model.safetensors

echo "[$(date +%T)] target=$OUT total=$TOTAL"
for i in $(seq 1 300); do
  sz=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
  if [ "$sz" -ge "$TOTAL" ]; then
    echo "[$(date +%T)] download complete: $sz bytes"
    break
  fi
  echo "[$(date +%T)] attempt #$i resuming at $sz/$TOTAL ($(( sz * 100 / TOTAL ))%)"
  curl -L -C - --fail-early --connect-timeout 30 --speed-limit 20480 --speed-time 60 \
       --retry 3 --retry-delay 5 --no-progress-meter -o "$OUT" "$URL"
  rc=$?
  echo "[$(date +%T)] curl rc=$rc size=$(stat -c%s "$OUT" 2>/dev/null || echo 0)"
  [ $rc -eq 0 ] || sleep 5
done

sz=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
if [ "$sz" -ne "$TOTAL" ]; then
  echo "[$(date +%T)] FAILED: size $sz != $TOTAL"
  exit 1
fi

echo "[$(date +%T)] verifying sha256 (this takes a few minutes on 14GB)..."
got=$(sha256sum "$OUT" | cut -d' ' -f1)
echo "[$(date +%T)] sha256=$got"
if [ "$got" != "$SHA" ]; then
  echo "[$(date +%T)] FAILED: sha256 mismatch, expected $SHA"
  exit 1
fi

HUB=/data/home/yuntao/.cache/huggingface/hub/models--lerobot--pi05_base
mv "$OUT" "$HUB/blobs/$SHA"
echo "[$(date +%T)] installed blob: $HUB/blobs/$SHA"
du -sh "$HUB"
echo "[$(date +%T)] ALL DONE"
