import json
from collections import Counter

with open("timur_cn_live_status.json") as f:
    data = json.load(f)

pt35 = data["pt_35"]
other_pt = data["other_pt"]

print(f"--- 291 Items with processingTime = 35 (NEED 12 WEEKS) ---")
by_factory = Counter(it["factory_name"] for it in pt35)
for fact, cnt in by_factory.most_common():
    print(f"  {fact:<30}: {cnt} items")

print("\nBy supplier:")
by_supplier = Counter(it["supplier"] for it in pt35)
for supp, cnt in by_supplier.most_common():
    print(f"  {supp:<30}: {cnt} items")

print("\nSample items currently at 35 days:")
for it in pt35[:8]:
    print(f"  SKU: {it['sku']} | Supp: {it['supplier']} | Factory: {it['factory_name']} | Title: {it['title'][:60]}")

print(f"\n--- 17 Items with OTHER processingTime ---")
other_pt_vals = Counter(it.get("live_pt") for it in other_pt)
print("Other PT values:", other_pt_vals)
for it in other_pt:
    print(f"  SKU: {it['sku']} | PT: {it.get('live_pt')} | Profile: {it.get('live_prof')} | Supp: {it['supplier']} | Title: {it['title'][:50]}")
