import json
import re

# Load all 11,291 updated CN SKUs
all_cn_skus = set()
sku_origins = {}

# 1. Main CN
with open("cn_products_xl.json") as f:
    d = json.load(f)
    for fact in d.get("factories", []):
        fname = fact.get("factory_name", "")
        for it in fact.get("items", []):
            s = str(it.get("ean") or it.get("sku") or "").strip()
            if s:
                all_cn_skus.add(s)
                sku_origins[s] = f"Main CN Factory: {fname}"

# 2. Mixed Batch 1 (756)
with open("cn_in_tr_batches_found.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        s = str(it.get("ean") or it.get("sku") or "").strip()
        if s:
            all_cn_skus.add(s)
            sku_origins[s] = f"Mixed Batch 1: {it.get('supplier')} ({it.get('reason')})"

# 3. Timur Batch 2 (308)
with open("timur_cn_to_update_12w.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        s = str(it.get("ean") or it.get("sku") or "").strip()
        if s:
            all_cn_skus.add(s)
            sku_origins[s] = f"Timur Batch 2: {it.get('supplier')} in {it.get('factory_name')}"

print(f"Loaded {len(all_cn_skus)} total updated SKUs.")

# Check against Turkish factories list
with open("factories_xl.txt") as f:
    tr_text = f.read()

# Load pure Turkish factory files (batches 1, 2, 4, 5 without the mixed ones)
# Let's inspect suppliers of all 11,291
from collections import Counter
supp_counter = Counter()

# Suppliers in 756
with open("cn_in_tr_batches_found.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        supp_counter[f"[Batch 1] {it.get('supplier')}"] += 1

# Suppliers in 308
with open("timur_cn_to_update_12w.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        supp_counter[f"[Batch 2] {it.get('supplier')}"] += 1

print("\nBreakdown of extracted Chinese items from mixed folders:")
for k, v in supp_counter.most_common():
    print(f"  {k:<45}: {v} items")

# Check if ANY Turkish keyword exists in reasons or suppliers
TURKISH_MARKERS = [
    "inegol", "inegöl", "bursa", "kayseri", "istanbul", "ankara", "turkey", "türkei",
    "koltuk", "mobilya", "ceviz", "baza", "istikbal", "bellona", "yatas", "dogtas",
    "kelebek", "enza", "alber", "alite", "redof", "soffi", "nills", "bussy", "titi",
    "arnisa", "onno", "muzaffer", "pinus", "vettero", "burobig", "taylan", "dengema",
    "alpino", "woodface", "sitis", "assra", "hmms", "luxe_life", "asfa", "dixon", "braw"
]

suspicious = []
for s, reason in sku_origins.items():
    r_low = reason.lower()
    for tr in TURKISH_MARKERS:
        if f" {tr} " in f" {r_low} " or f"_{tr}_" in r_low or f"/{tr}/" in r_low:
            suspicious.append((s, tr, reason))

print(f"\nSuspicious matches against Turkish keywords: {len(suspicious)}")
for s, tr, r in suspicious[:10]:
    print(f"  SKU: {s} | Keyword: {tr} | Origin: {r}")
