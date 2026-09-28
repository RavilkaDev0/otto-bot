import json

with open("all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

# Load known TR factories from factories_xl.txt
with open("factories_xl.txt") as f:
    tr_txt = f.read()

# Load known CN factories
with open("all_chinese_factories_xl.txt") as f:
    cn_txt = f.read()

unclassified = []
for f in facts:
    name = f.get("name", "").strip()
    fid = f.get("id")
    if name in tr_txt or name in cn_txt:
        continue
    # Not in TR and not in CN
    unclassified.append((fid, name))

print(f"Total unclassified factories (neither in TR list nor in CN list): {len(unclassified)}")
for fid, name in sorted(unclassified, key=lambda x: x[1]):
    print(f"  ID: {fid:<7} | {name}")
