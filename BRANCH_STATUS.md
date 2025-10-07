# Branch Status: emap-postgres-cbc-lut-map

## Summary

This branch successfully combines:
- PostgreSQL/CBC backend support from `emap-postgres-cbc`
- LUT mapping functionality from `lut-map`
- Optimized LUT mapper with caching and greedy coverage

## What Works

### ✅ Word-Level Benchmarks (FIR, Systolic)
- Can load and process FIR/systolic designs
- Supports equality saturation on word-level cells ($add, $mul, $dff)
- Uses word-level NetlistDB with RollingHash
- Schema: wirevecs, aby_cells, ay_cells, dffs, etc.

**Example**:
```python
import emap
netlist = emap.NetlistDB('emap/schema.sql')
with open('eval/out/fir_n16_w8.json') as f:
    netlist.build_from_json(json.load(f)['modules']['top'])
# Works! Loads 80 cells, 1034 wires
```

### ✅ AIG Benchmarks (EPFL Adder) + LUT Mapping
- Can load AIG designs (AND/INV gates only)
- Supports LUT mapping on bit-level netlist
- Uses AIG NetlistDB with XorHash (see emap/db_aig.py)
- Schema: ands, invs, luts, wiresets, etc.

**Note**: Currently the AIG version is in `db_aig.py` for reference, but the factory
function routes to the word-level db.py. To use AIG+LUTs, must import directly from db_aig.

## What Doesn't Work Yet

### ❌ LUT Mapping on FIR/Systolic
**Problem**: Two incompatible database schemas:
- Word-level DB: Uses `wirevecs` (multi-bit wire vectors), `aby_cells` (word-level ops)
- LUT mapper: Expects `ands`, `invs`, `wiresets` (bit-level AIG)

**Current workaround**: Only tested LUT mapping on EPFL adder (AIG format)

**Future solution**: Need to extract bit-level AIG from word-level e-graph after equality saturation

## Database Schemas

### Word-Level Schema (emap/db.py)
```sql
wirevecs (id, hash)
wirevec_members (wirevec, idx, wire)
aby_cells (type, a, b, y)  -- $add, $mul, etc.
ay_cells (type, a, y)       -- $not, $neg, etc.
dffs (d, q)
from_inputs (source, name)  -- source = wirevec id
as_outputs (sink, name)     -- sink = wirevec id
```

### AIG Schema (emap/db_aig.py, lut mapper expects)
```sql
ands (a, b, y)
invs (a, y)
wiresets (id, hash)
wireset_members (wireset_id, wire_id)
luts (ins, out)             -- ins = wireset id
from_inputs (source, name)  -- source = wire id
as_outputs (sink, name)     -- sink = wire id
```

## Files

- `emap/db.py`: Word-level NetlistDB (restored from emap-postgres-cbc)
- `emap/db_aig.py`: AIG-only NetlistDB (from lut-map, for reference)
- `emap/utils.py`: Has both `RollingHash` (word-level) and `XorHash` (AIG)
- `emap/rewrites/lut.py`: LUT mapper (expects AIG schema)

## Recommendations

### Short Term
Continue evaluating LUT mapper on AIG benchmarks (EPFL suite).

### Medium Term
Add bit-blasting or AIG extraction from word-level e-graph:

```python
# Hypothetical API
netlist_wordlevel = emap.NetlistDB('emap/schema.sql')
netlist_wordlevel.build_from_json(fir_data)
netlist_wordlevel.rebuild()  # Equality saturation

# Extract AIG representation
netlist_aig = netlist_wordlevel.extract_aig()
emap.rewrites.techmap_luts(netlist_aig, k=6, cnt=100, rseed=42)
```

### Long Term
Unified database schema that supports both word-level and bit-level views,
or make LUT mapper work directly on word-level e-graph.

## Testing

**Word-level loading**:
```bash
python3 -c "import emap, json; n = emap.NetlistDB('emap/schema.sql'); \
    n.build_from_json(json.load(open('eval/out/fir_n16_w8.json'))['modules']['top'])"
# ✅ Works: Loads FIR
```

**LUT mapping (AIG only)**:
```bash
python3 compare_lut_mapping.py
# ✅ Works: Tests on EPFL adder with greedy/random LUT mapping
```

**LUT mapping on FIR** (doesn't work yet):
```bash
python3 compare_lut_fir.py
# ❌ Fails: Schema mismatch (no ands/invs/luts tables in word-level DB)
```

## Git History

```
* 5e33e1a Fix word-level NetlistDB support after lut-map merge
* 1ef025d Optimize LUT mapper for better QOR and performance
* 50c81c4 Merge lut-map into emap-postgres-cbc
```

## Conclusion

The branch successfully combines both capabilities, but they use different
database schemas. LUT mapping works great on AIG benchmarks. To enable
LUT mapping on FIR/systolic, need to add AIG extraction from word-level e-graph.
