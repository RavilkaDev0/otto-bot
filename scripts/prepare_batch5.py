import json
from collections import defaultdict

with open("hidden_tr_found_unmarked.json", "r", encoding="utf-8") as f:
    data = json.load(f)

items = data["items"]

# Group by folder / brand
by_brand = defaultdict(list)
for it in items:
    brand = it["tr_brand"].upper()
    by_brand[brand].append({
        "sku": it["sku"],
        "ean": it["ean"],
        "title": it["title"],
        "factory_name": brand,
        "folder_name": it["folder_name"]
    })

factories_list = []
for brand, its in sorted(by_brand.items(), key=lambda x: len(x[1]), reverse=True):
    factories_list.append({
        "factory_name": f"{brand}_(TR)",
        "factory_id": "SORTIERUNG",
        "items_count": len(its),
        "items": its
    })

out_payload = {
    "account": "xl",
    "total_new_items": len(items),
    "factories": factories_list
}

with open("tr_products_xl_batch5.json", "w", encoding="utf-8") as f:
    json.dump(out_payload, f, ensure_ascii=False, indent=2)

print(f"Created tr_products_xl_batch5.json with {len(items)} items across {len(factories_list)} factories.")
