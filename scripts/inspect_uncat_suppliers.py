import json

with open("all_timur_suppliers_catalog.json") as f:
    supps = json.load(f)

# Print all 96 suppliers that weren't categorized as TR or CN
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
    "saloni", "burosit", "bellona", "istikbal", "mondi", "dogtas", "kelebek", "enza",
    "area", "wossew", "canpe", "mre", "odac", "mobiant", "bakart", "evsane", "rvt",
    "emek", "yme", "modelin", "modasel", "aren", "hitit", "regnum", "mbss", "inci",
    "boyneroglu", "lidya", "redof", "tusca", "gencdoor", "arredoclassic"
]

CN_MARKERS = [
    "china", "chinese", "cn", "lenatees", "jingyong", "yafulai", "kuka",
    "mingcasa", "youme", "alibaba", "ganasi", "pengpai", "youbond", "yqc",
    "evergo", "weitao", "bailong", "defaico", "biaodi", "kadi", "oubaolong",
    "haoyu", "hengxuan", "milliard", "foshan", "dongguan", "shenzhen", "guangzhou",
    "shunde", "lecong", "longjiang", "nanhai", "anji", "hangzhou", "ningbo"
]

uncat = []
for s in supps:
    name_low = s["supplier"].lower()
    urls_str = " ".join(s["sample_urls"]).lower()
    is_cn = any(m in name_low or f"/{m}/" in urls_str for m in CN_MARKERS)
    is_tr = any(m in name_low for m in TR_MARKERS)
    if not is_cn and not is_tr:
        uncat.append(s)

print(f"Remaining uncategorized suppliers: {len(uncat)}")
for s in uncat:
    print(f"{s['supplier']:<25} ({s['count']:3d} items) | Sample URL: {s['sample_urls'][:1]} | Sample Title: {s['sample_titles'][:1]}")
