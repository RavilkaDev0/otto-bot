import json

with open("all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

print(f"Total factories in all_afterbuy_xl_factories.json: {len(facts)}")

# Check which factories are NOT in Turkish (factories_xl.txt) and NOT in Chinese (factories_xl_cn.txt)
with open("factories_xl_cn.txt") as f:
    cn_text = f.read()

with open("factories_xl.txt") as f:
    tr_text = f.read()

# Check names of all factories
unclassified = []
for f in facts:
    fname = f.get("name", "").strip()
    fid = f.get("id")
    if fname not in cn_text and fname not in tr_text:
        unclassified.append((fid, fname))

print(f"Factories not in CN list and not in TR list: {len(unclassified)}")
for fid, fn in unclassified[:40]:
    print(f"  ID: {fid} | Name: {fn}")
