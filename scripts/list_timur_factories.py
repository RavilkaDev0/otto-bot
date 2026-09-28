import json

with open("all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

timur_facts = [f for f in facts if "timur" in f.get("name", "").lower()]
print(f"Total Timur factories: {len(timur_facts)}")
for f in timur_facts:
    print(f"ID: {f.get('id')}, Name: {f.get('name')}")
