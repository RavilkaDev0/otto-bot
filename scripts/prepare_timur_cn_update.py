import json

with open("timur_cn_live_status.json") as f:
    data = json.load(f)

pt35 = data.get("pt_35", [])
other_pt = data.get("other_pt", [])

# Combine items needing update to 12 weeks
to_update = pt35 + other_pt

print(f"Total items to update to 12 weeks: {len(to_update)}")
print(f"  - Currently 35 days (4-8w): {len(pt35)}")
print(f"  - Currently default: {len(other_pt)}")

# Ensure unique SKUs
seen = set()
unique_items = []
for it in to_update:
    s = str(it.get("sku") or it.get("ean") or "").strip()
    if s and s not in seen:
        seen.add(s)
        unique_items.append(it)

print(f"Total unique SKUs to update: {len(unique_items)}")

# Breakdown by factory
from collections import Counter
by_factory = Counter(it["factory_name"] for it in unique_items)
print("\nBreakdown by factory:")
for fact, cnt in by_factory.most_common():
    print(f"  {fact:<30}: {cnt} items")

# Breakdown by supplier
by_supp = Counter(it["supplier"] for it in unique_items)
print("\nBreakdown by supplier:")
for supp, cnt in by_supp.most_common():
    print(f"  {supp:<30}: {cnt} items")

with open("timur_cn_to_update_12w.json", "w", encoding="utf-8") as out:
    json.dump({
        "total_to_update": len(unique_items),
        "target_processing_time": "57",
        "target_shipping_profile": "027a1a46-cf60-56f4-917b-81661569f016",
        "breakdown_factory": dict(by_factory),
        "breakdown_supplier": dict(by_supp),
        "items": unique_items
    }, out, indent=2, ensure_ascii=False)

print("\nSaved to timur_cn_to_update_12w.json")
