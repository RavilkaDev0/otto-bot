import json
from collections import Counter, defaultdict

with open("timur_factories_audit.json") as f:
    data = json.load(f)

print(f"Total CN detected in Timur factories: {len(data['cn_items'])}")

# Count CN by supplier
cn_by_supp = Counter(it["supplier"] for it in data["cn_items"])
print("\n--- CN items by supplier ---")
for s, c in cn_by_supp.most_common():
    print(f"  {s:<35}: {c}")

# Let's inspect the suppliers across all factories
all_suppliers = Counter()
for fact in data["factories"]:
    for s, c in fact["suppliers"].items():
        all_suppliers[s] += c

print("\n--- All Top Suppliers in Timur factories ---")
for s, c in all_suppliers.most_common(50):
    print(f"  {s:<35}: {c}")
