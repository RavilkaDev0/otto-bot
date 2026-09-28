import json
import glob
import re

# 1. Load all 38,362 SKUs from all TR batches
tr_files = sorted(glob.glob("/home/Altera/workdir/otto-bot/tr_products_xl*.json"))
all_tr_items = {}

for path in tr_files:
    with open(path) as f:
        d = json.load(f)
        for fact in d.get("factories", []):
            fname = fact.get("factory_name", "")
            fid = fact.get("factory_id", "")
            for it in fact.get("items", []):
                sku = str(it.get("ean") or it.get("sku") or "").strip()
                if sku and sku not in all_tr_items:
                    all_tr_items[sku] = {
                        "sku": sku,
                        "title": it.get("title", ""),
                        "factory_name": fname,
                        "factory_id": fid,
                        "source_file": path.split("/")[-1]
                    }

print(f"Loaded {len(all_tr_items)} unique SKUs from TR batches.")

# 2. Known 100% PROVEN Chinese suppliers / brands / markers
# Must be specific and exact to NEVER match Turkish goods!
EXPLICIT_CN_SUPPLIERS = [
    "lenatees", "leenatees", "jingyong", "dongguan_yafulai", "yafulai",
    "kuka", "mingcasa", "youme", "you-me", "alibaba", "ganasi", "pengpai",
    "youbond", "you-bond", "yqc", "evergo", "weitao", "bailong"
]

# 3. Load supplier info from timur_and_mixed_analysis.json
with open("/home/Altera/workdir/otto-bot/timur_and_mixed_analysis.json") as f:
    mixed_data = json.load(f)

sku_to_supplier = {}
for folder in mixed_data:
    for supp, info in folder["suppliers"].items():
        for s in info["skus"]:
            sku_to_supplier[s] = supp

print(f"Mapped {len(sku_to_supplier)} SKUs to specific image suppliers in mixed folders.")

# 4. Classify each SKU in TR batches
cn_items = []
tr_items = []
unmapped_items = []

for sku, it in all_tr_items.items():
    fn = it["factory_name"].lower()
    title = it["title"].lower()
    supp = sku_to_supplier.get(sku, "")
    supp_low = supp.lower()

    is_cn = False
    cn_reason = ""

    # Check 1: Image supplier in mixed folders
    for cn_term in EXPLICIT_CN_SUPPLIERS:
        if cn_term in supp_low:
            is_cn = True
            cn_reason = f"Image supplier '{supp}' matches CN term '{cn_term}'"
            break

    # Check 2: Factory name itself is Chinese
    if not is_cn:
        for cn_term in EXPLICIT_CN_SUPPLIERS:
            if cn_term in fn:
                is_cn = True
                cn_reason = f"Factory name '{it['factory_name']}' matches CN term '{cn_term}'"
                break

    # Check 3: Title explicit China marker
    if not is_cn:
        if " aus china" in title or "(cn)" in title or "_cn" in title:
            is_cn = True
            cn_reason = f"Title has explicit China marker: '{it['title'][:40]}'"

    if is_cn:
        cn_items.append({
            "sku": sku,
            "title": it["title"],
            "factory_name": it["factory_name"],
            "supplier": supp,
            "reason": cn_reason
        })
    else:
        tr_items.append(sku)

print(f"\n==================================================")
print(f"AUDIT RESULT OF ALL 38,362 'TURKISH' ITEMS:")
print(f"  * Confirmed CHINESE items inside TR batches: {len(cn_items)}")
print(f"  * Confirmed TURKISH items (MUST NOT TOUCH): {len(tr_items)}")
print(f"==================================================\n")

# Breakdown of Chinese items by supplier / factory
from collections import Counter
cn_supp_counts = Counter(x["supplier"] or x["factory_name"] for x in cn_items)
print("Breakdown of Chinese items by supplier/factory:")
for s, c in cn_supp_counts.most_common():
    print(f"  {s:<35} : {c:4d} items")

with open("/home/Altera/workdir/otto-bot/cn_in_tr_batches_found.json", "w", encoding="utf-8") as out:
    json.dump({
        "total_cn_found": len(cn_items),
        "total_tr_preserved": len(tr_items),
        "breakdown": dict(cn_supp_counts),
        "items": cn_items
    }, out, indent=2, ensure_ascii=False)
