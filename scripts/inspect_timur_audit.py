import json
from collections import Counter

with open("timur_factories_audit.json") as f:
    data = json.load(f)

print(f"Summary: {data['summary']}")

other_suppliers = Counter()
for fact in data["factories"]:
    print(f"\n--- {fact['factory_name']} (Total: {fact['total_items']}) ---")
    print(f"  CN: {fact['cn_count']}, TR: {fact['tr_count']}, Other: {fact['other_count']}")
    print(f"  Suppliers: {fact['suppliers']}")

# Let's inspect other_samples
print("\n--- Sample Other/Uncertain items ---")
for fact in data["factories"]:
    if fact.get("other_samples"):
        print(f"\nFactory: {fact['factory_name']}")
        for s in fact["other_samples"][:3]:
            print(f"  SKU: {s['sku']} | Supplier: {s['supplier']} | Title: {s['title'][:50]} | GURL: {s['gurl']}")
