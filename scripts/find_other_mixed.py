import json

with open("all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

targets = ["kuka", "new_ropez", "mixfabrik", "others_mix"]
for f in facts:
    low = f.get("name", "").lower()
    if any(t in low for t in targets):
        print(f"ID: {f.get('id')}, Name: {f.get('name')}")
