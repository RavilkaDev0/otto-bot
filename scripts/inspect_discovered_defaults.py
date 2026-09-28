import json

with open("newly_discovered_cn_items.json") as f:
    items = json.load(f)

print(f"Total discovered items: {len(items)}")

defaults = [it for it in items if it.get("live_pt") == "DEFAULT"]
print(f"Items with live_pt == DEFAULT: {len(defaults)}")
for it in defaults:
    print(f"  SKU: {it['sku']} | Profile: {it.get('live_prof')} | Folder: {it['factory_name']} | Title: {it['title'][:60]}")
