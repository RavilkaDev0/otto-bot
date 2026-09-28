import json
from collections import Counter

with open("timur_factories_audit.json") as f:
    data = json.load(f)

# Collect all suppliers and their sample URLs and titles
supplier_info = {}
for fact in data["factories"]:
    fname = fact["factory_name"]
    fid = fact["factory_id"]
    # We can inspect items directly by fetching from Afterbuy or if we have items
