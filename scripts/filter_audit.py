import json
from collections import Counter

with open("/home/Altera/workdir/otto-bot/confirmed_missed_db_xl.json", "r", encoding="utf-8") as f:
    items = json.load(f)

# Exclude false-positive substrings from factory names that happen to be common words
# e.g. "kristal" in Kristall-Design, "classi" in arredoclassic, "sdesign" in Luxusdesign
BAD_SUBSTRINGS = {"kristal", "classi", "sdesign", "lux", "art", "stil"}

filtered = []
for it in items:
    reason = it.get("reason", "")
    is_bad = False
    for bad in BAD_SUBSTRINGS:
        if f"'{bad}'" in reason or f"'{bad}" in reason:
            is_bad = True
            break
    if not is_bad:
        filtered.append(it)

print(f"Total raw: {len(items)}")
print(f"Total filtered: {len(filtered)}\n")

reasons = Counter()
for it in filtered:
    reasons[it.get("reason")] += 1

print("--- Filtered Reasons ---")
for r, c in reasons.most_common(20):
    print(f"  {c:4d} | {r}")

print("\n--- Filtered Sample (first 10) ---")
for it in filtered[:10]:
    print(f"  SKU: {it['ean']} | Reason: {it.get('reason')} | Title: {it.get('title')}")

with open("/home/Altera/workdir/otto-bot/filtered_missed_xl.json", "w", encoding="utf-8") as f:
    json.dump(filtered, f, ensure_ascii=False, indent=2)
