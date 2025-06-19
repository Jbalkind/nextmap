import nextmap
from nextmap.db_aig import NetlistDB
from nextmap.rewrites import aig_opt
import json
import time

TEST_NAME = "adder"
TOP_MODULE = "eval/epfl/adder"
MAX_ITER = 4

start_time = time.time()

netlist = NetlistDB(schema_file="nextmap/schema.sql", db_file=":memory:", cnt=10000)
netlist.VERBOSE = True
with open(f"eval/epfl/{TEST_NAME}.json") as f:
    netlist.build_from_json(json.load(f)["modules"][TOP_MODULE])

wdsu = nextmap.DisjointSetUnion()
netlist.rebuild(wdsu)

for i in range(MAX_ITER):
    cnt = aig_opt.optimize_aig_with_eqsat(netlist, max_iterations=1)['total_rewrites']

    if cnt > 0:
        print(f"Applied {cnt} rewrites")
        netlist.rebuild(wdsu)
    else:
        print("No more rewrites can be applied. Stopping.")
        break

# lut map
nextmap.rewrites.techmap_luts(netlist, k=6, cnt=100, rseed=42)

with open("debug.json", "w") as f:
    json.dump(netlist.dump_tables(), f, indent=2)

with open(f"eval/out/saturated_{TEST_NAME}.json", "w") as f:
    json.dump({"creator": "nextmap", "modules": {"top": netlist.write_json()}}, f, indent=2)