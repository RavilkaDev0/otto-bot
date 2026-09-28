import json

with open("extra_cn_factories_status.json") as f:
    data = json.load(f)

need_update = data.get("need_update", [])
print(f"Total items needing update: {len(need_update)}")

seen = set()
unique_items = []
for it in need_update:
    s = str(it.get("sku") or it.get("ean") or "").strip()
    if s and s not in seen:
        seen.add(s)
        unique_items.append({
            "sku": s,
            "title": it.get("title"),
            "factory_name": it.get("factory_name"),
            "factory_id": it.get("factory_id"),
            "live_pt": it.get("live_pt"),
            "live_prof": it.get("live_prof")
        })

print(f"Total unique SKUs: {len(unique_items)}")

with open("extra_cn_385_to_update_12w.json", "w", encoding="utf-8") as out:
    json.dump({
        "total_to_update": len(unique_items),
        "target_processing_time": "57",
        "target_shipping_profile": "027a1a46-cf60-56f4-917b-81661569f016",
        "items": unique_items
    }, out, indent=2, ensure_ascii=False)

print("Saved to extra_cn_385_to_update_12w.json")
