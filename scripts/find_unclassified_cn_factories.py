import json
import re

with open("all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

with open("factories_xl_cn.txt") as f:
    cn_text = f.read()

CN_TERMS = [
    "ganasi", "pengpai", "youbond", "you-bond", "youme", "you-me", "lenatees",
    "kuka", "yqc", "evergo", "mefan", "weitao", "bailong", "defaico", "biaodi",
    "kadi", "oubaolong", "haoyu", "hengxuan", "milliard", "fendi", "romero",
    "china", "chinesisch", "foshan", "dongguan", "shenzhen", "guangzhou", "shunde",
    "anji", "hangzhou", "ningbo", "suzhou", "wuxi", "hebei", "shengfang", "bazhou"
]

matched_facts = []
for f in facts:
    fname = f.get("name", "").strip()
    fid = f.get("id")
    flow = fname.lower()
    
    # If already in cn_text, skip
    if fname in cn_text:
        continue
        
    for t in CN_TERMS:
        if t in flow:
            matched_facts.append((fid, fname, t))
            break

print(f"Found {len(matched_facts)} unclassified factories matching Chinese terms:")
for fid, fn, t in matched_facts:
    print(f"  ID: {fid} | Name: {fn:<45} | Matched: {t}")
