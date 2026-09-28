import json

with open("all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

for f in facts:
    if str(f.get("id")) in ["481815", "481654", "481810"]:
        print(f"ID: {f['id']} | Name: '{f['name']}' | Items count: {f.get('items_count')}")
