import json

with open("hidden_tr_items_xl.json") as f:
    data = json.load(f)

print(f"Total factories in hidden_tr_items_xl.json: {len(data['factories'])}")
for fact in data["factories"]:
    fn = fact["factory_name"]
    cnt = fact["items_count"]
    # Check sample items and matched_kw
    samples = fact["items"][:2]
    print(f"\nFolder: '{fn}' ({cnt} items):")
    for s in samples:
        print(f"  - [{s.get('matched_kw')}] {s.get('title')[:40]} | {s.get('pic')[:60]}")
