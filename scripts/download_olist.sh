#!/usr/bin/env bash
# Olist Brazilian E-Commerce (Kaggle: olistbr/brazilian-ecommerce, CC BY-NC-SA 4.0), HF mirror whose
# file sizes and SHA-256 match the Kaggle originals (cross-checked with a second mirror, MafiaAzulBr/olistcsv).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw/olist
HF=https://huggingface.co/datasets/aviahYadler/Olist_Ecommerce_Dataset/resolve/main
for f in olist_customers_dataset olist_geolocation_dataset olist_order_items_dataset olist_order_payments_dataset \
         olist_order_reviews_dataset olist_orders_dataset olist_products_dataset olist_sellers_dataset \
         product_category_name_translation; do
  [ -f "data/raw/olist/$f.csv" ] || curl -sL -o "data/raw/olist/$f.csv" "$HF/$f.csv"
done
(cd data/raw/olist && shasum -a 256 -c ../../../scripts/olist_sha256.txt)
