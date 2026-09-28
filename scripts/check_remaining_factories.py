import json
import re

with open("all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

print(f"Total factories in all_afterbuy_xl_factories.json: {len(facts)}")

# Load the 63 factories already identified in all_chinese_factories_xl.txt
already_known_factories = set()
with open("all_chinese_factories_xl.txt") as f:
    for line in f:
        m = re.search(r"^\s*\d+\.\s+([A-Za-z0-9_(). -]+?)(?:\s+\[ID:|\s+—)", line)
        if m:
            already_known_factories.add(m.group(1).strip())

print(f"Already known factories in all_chinese_factories_xl.txt: {len(already_known_factories)}")

# Check remaining factory names in Afterbuy
CN_TERMS = [
    "china", "chines", "cn", "foshan", "dongguan", "shenzhen", "guangzhou",
    "shunde", "lecong", "anji", "hangzhou", "ningbo", "ganasi", "biaodi",
    "yqc", "defaico", "evergo", "kadi", "lenatees", "pengpai", "youmi",
    "weitao", "kuka", "mingcasa", "yafulai", "youbond", "youme", "mefan",
    "xingsheng", "sunray", "zhiha", "zhimu", "lvteng", "hanting", "xinyilong"
]

remaining_facts = []
suspicious_facts = []
for f in facts:
    fname = f.get("name", "").strip()
    fid = f.get("id")
    if fname in already_known_factories:
        continue
    remaining_facts.append(f)
    fname_low = fname.lower()
    for t in CN_TERMS:
        if f" {t} " in f" {fname_low} " or f"_{t}_" in fname_low or fname_low.startswith(f"{t}_") or fname_low.endswith(f"_{t}") or f"({t})" in fname_low or f"_{t}" in fname_low:
            suspicious_facts.append((fname, fid, t))
            break

print(f"Remaining untouched factories: {len(remaining_facts)}")
print(f"Suspicious factory names with Chinese keywords: {len(suspicious_facts)}")
for fn, fid, t in suspicious_facts:
    print(f"  * [ID: {fid}] {fn} (matched '{t}')")
