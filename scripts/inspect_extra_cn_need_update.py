import json
from collections import Counter

with open("extra_cn_factories_status.json") as f:
    data = json.load(f)

need_update = data.get("need_update", [])
print(f"Total items needing update: {len(need_update)}")

by_factory = Counter(it["factory_name"] for it in need_update)
for fn, c in by_factory.most_common():
    print(f"  {fn:<35}: {c} items")

print("\nSample items:")
for it in need_update[:5]:
    print(f"  SKU: {it['sku']} | PT: {it.get('live_pt')} | Factory: {it['factory_name']} | Title: {it['title']}")
