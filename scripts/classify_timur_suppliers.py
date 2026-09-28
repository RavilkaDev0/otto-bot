import json
import re

with open("all_timur_suppliers_catalog.json") as f:
    supps = json.load(f)

print(f"Total suppliers: {len(supps)}")

# Look for any potential Chinese keywords or non-Turkish names
potential_cn = []
known_tr = []
others = []

# Turkish indicators
TR_MARKERS = [
    "tr", "mobilya", "koltuk", "kayseri", "inegol", "bursa", "istanbul", "ankara",
    "home", "office", "design", "living", "sofa", "garden", "wood", "concept",
    "alpino", "burobig", "vettero", "rixxe", "trio", "erika", "evart", "duga",
    "serhat", "dengema", "cel", "taylan", "nott", "familygroup", "pisa", "hilal",
    "braw", "rotta", "loyo", "katre", "eymense", "tpt", "azr", "rgnm", "ezwood",
    "moi", "zeence", "dudi", "mono", "acca", "merli", "ruufform", "essence",
    "somm", "epika", "brezza", "guencel", "arnisa", "woodface", "sitis", "muzaffer",
    "mozaffer", "assra", "hmms", "luxe_life", "luxen", "newmood", "newstar",
    "asfa", "dixon", "pmc", "moon", "al_elik", "alt", "celik", "med", "cagin",
    "saloni", "burosit", "bellona", "istikbal", "mondi", "dogtas", "kelebek", "enza"
]

CN_MARKERS = [
    "china", "chinese", "cn", "lenatees", "jingyong", "yafulai", "kuka",
    "mingcasa", "youme", "alibaba", "ganasi", "pengpai", "youbond", "yqc",
    "evergo", "weitao", "bailong", "defaico", "biaodi", "kadi", "oubaolong",
    "haoyu", "hengxuan", "milliard", "foshan", "dongguan", "shenzhen", "guangzhou",
    "shunde", "lecong", "longjiang", "nanhai", "anji", "hangzhou", "ningbo"
]

for s in supps:
    name = s["supplier"]
    name_low = name.lower()
    urls_str = " ".join(s["sample_urls"]).lower()
    titles_str = " ".join(s["sample_titles"]).lower()
    all_str = f"{name_low} {urls_str} {titles_str}"

    is_cn = any(m in name_low or f"/{m}/" in urls_str or f"_{m}_" in urls_str for m in CN_MARKERS)
    if is_cn:
        potential_cn.append(s)
    else:
        # Check if clearly Turkish
        is_tr = any(m in name_low for m in TR_MARKERS)
        if is_tr:
            known_tr.append(s)
        else:
            others.append(s)

print(f"\n--- Confirmed / Potential CHINESE Suppliers ({len(potential_cn)}) ---")
cn_item_sum = sum(s["count"] for s in potential_cn)
for s in potential_cn:
    print(f"  * {s['supplier']:<35} : {s['count']:4d} items | URLs: {s['sample_urls'][:1]}")
print(f"Total items under CN suppliers: {cn_item_sum}")

print(f"\n--- Other / Unclassified Suppliers ({len(others)}) ---")
other_item_sum = sum(s["count"] for s in others)
for s in others[:30]:
    print(f"  * {s['supplier']:<35} : {s['count']:4d} items | URLs: {s['sample_urls'][:1]}")
print(f"Total items under Other suppliers: {other_item_sum}")
