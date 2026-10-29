#!/usr/bin/env bash
# BIRD Mini-Dev: SQLite databases (2024 package, unchanged DB contents) + 2026 "Mini-Dev 1024" annotations
# from the official Hugging Face dataset. License CC BY-SA 4.0. ~800 MB download, ~1.4 GB extracted (SQLite only).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw/bird data/raw/bird_hf
if [ ! -d data/raw/bird/dev/dev_databases ]; then
  curl -L -o data/raw/bird/minidev.zip https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip
  (cd data/raw/bird && unzip -q minidev.zip "minidev/MINIDEV/*" && mv minidev/MINIDEV ./dev && rmdir minidev && rm minidev.zip)
fi
HF=https://huggingface.co/datasets/birdsql/bird_mini_dev/resolve/main
for f in data/mini_dev_sqlite.jsonl data/manifest.json baseline_inference/mini_dev_sqlite.jsonl \
         baseline_inference/prompt_template.txt archive/bird_mini_dev_2025-07-04/data/mini_dev_sqlite-00000-of-00001.json; do
  [ -f "data/raw/bird_hf/$f" ] || curl -sL --create-dirs -o "data/raw/bird_hf/$f" "$HF/$f"
done
# full BIRD dev set (1,534 pairs on the same 11 DBs) -> decontaminated few-shot pool; keep only dev.json
if [ ! -f data/raw/bird/bird_dev_full/dev.json ]; then
  curl -L -o data/raw/bird/dev_full.zip https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip
  (cd data/raw/bird && unzip -q -o -j dev_full.zip dev_20240627/dev.json -d bird_dev_full && rm dev_full.zip)
fi
echo "BIRD ready"
