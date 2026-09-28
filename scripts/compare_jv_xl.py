import json
import re

# Read JV factories
with open("/home/Altera/workdir/otto-bot/factories.txt") as f:
    jv_lines = f.readlines()

jv_factories = {}
for line in jv_lines:
    m = re.match(r"^\d+\.\s+([^\s—]+(?:\s+[^\s—]+)*)\s+—\s+(\d+)", line)
    if m:
        name = m.group(1).strip()
        # Clean annotations like " (Alpino)", " (из 2-й партии)"
        clean_name = re.sub(r"\s+\([^)]+\)", "", name).strip()
        count = int(m.group(2))
        jv_factories[clean_name] = count

# Read XL factories
with open("/home/Altera/workdir/otto-bot/factories_xl.txt") as f:
    xl_lines = f.readlines()

xl_factories = {}
for line in xl_lines:
    m = re.match(r"^\d+\.\s+([^\s—]+(?:\s+[^\s—]+)*)\s+—\s+(\d+)", line)
    if m:
        name = m.group(1).strip()
        count = int(m.group(2))
        xl_factories[name.lower()] = (name, count)

print("=== COMPARISON JV factories.txt VS XL factories_xl.txt ===\n")
print(f"Total JV factories: {len(jv_factories)}")
print(f"Total XL factories: {len(xl_factories)}\n")

missing_in_xl = {}
present_in_both = {}

for jv_name, jv_count in jv_factories.items():
    low = jv_name.lower().strip()
    if low in xl_factories:
        orig_xl_name, xl_count = xl_factories[low]
        present_in_both[jv_name] = (jv_count, xl_count)
    else:
        # Check fuzzy match
        found = False
        for xl_low, (orig_xl_name, xl_count) in xl_factories.items():
            if low.replace(" ", "_") == xl_low.replace(" ", "_"):
                present_in_both[jv_name] = (jv_count, xl_count)
                found = True
                break
        if not found:
            missing_in_xl[jv_name] = jv_count

print(f"--- 1. Factories present in JV but MISSING from factories_xl.txt ({len(missing_in_xl)}) ---")
for fn, jv_cnt in sorted(missing_in_xl.items(), key=lambda x: x[1], reverse=True):
    print(f"  * {fn} (JV had: {jv_cnt} items)")

print(f"\n--- 2. Factories present in BOTH ({len(present_in_both)}) ---")
for fn, (jc, xc) in sorted(present_in_both.items(), key=lambda x: x[1][1], reverse=True):
    print(f"  * {fn}: JV={jc} | XL={xc}")
