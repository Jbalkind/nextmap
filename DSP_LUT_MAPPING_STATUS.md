# DSP + LUT Mapping Analysis

## Executive Summary

Successfully analyzed the workflow for combining DSP mapping with LUT mapping on systolic benchmarks. The key finding is that **bitblasting is needed** to convert unmapped word-level logic to bit-level AIG before applying LUT mapping.

## What Works Now

### ✅ Full DSP Mapping Pipeline
```bash
source venv/bin/activate
python3 test_dsp_plus_lut_mapping.py
```

**Results on systolic_matmul_4x4_w8**:
- Loaded: 88 word-level cells (16 $add, 16 $mul, 16 $mux, 39 $dff)
- Applied 155 equality saturation rewrites
- Applied 16 SDFF rewrites
- DSP tech mapping completed (though 0 DSPs mapped due to match_sql error)
- ILP extraction successful with CBC solver
- **Extracted 87 cells**: 16 $add, 16 $mul, 16 $mux, 39 $dff

### ✅ LUT Mapping on AIG Benchmarks
- Works great on EPFL adder (10,464 AND/INV gates)
- Greedy mode achieves 3-4x better coverage than random
- Demonstrated improvements documented in `LUT_MAPPING_EVALUATION_RESULTS.md`

## The Gap: Bitblasting

### Problem
After DSP extraction, the unmapped logic is still in **word-level format**:
```
$add: 16 cells (8-bit addition)
$mul: 16 cells (8-bit multiplication)
$mux: 16 cells (8-bit multiplexer)
```

LUT mapper expects **bit-level AIG format**:
```
ands (a, b, y)  -- Single-bit AND gates
invs (a, y)     -- Single-bit inverters
```

### Solution Needed
Implement bitblasting to convert word-level cells to AIG:

```python
# Pseudo-code for bitblasting $add
def bitblast_add(a_vec: list[int], b_vec: list[int]) -> list[int]:
    """Convert 8-bit $add to ripple-carry adder with AND/XOR gates."""
    width = len(a_vec)
    sum_bits = []
    carry = 0  # constant wire

    for i in range(width):
        # sum[i] = a[i] ^ b[i] ^ carry
        # carry_out = (a[i] & b[i]) | (a[i] & carry) | (b[i] & carry)
        sum_bit = create_xor_gate(a_vec[i], b_vec[i], carry)
        carry = create_carry_gate(a_vec[i], b_vec[i], carry)
        sum_bits.append(sum_bit)

    return sum_bits
```

### Estimate
- 16 x 8-bit adders → ~16 * 8 * 3 = **384 AND gates**
- 16 x 8-bit multipliers → ~16 * 8 * 8 = **1,024 AND gates**
- 16 x 8-bit muxes → ~16 * 8 * 2 = **256 AND gates**
- **Total: ~1,664 AND gates** suitable for LUT mapping

## Proposed Workflow

```
1. Load systolic/FIR (word-level)
   ↓
2. Apply equality saturation rewrites
   ↓
3. Apply DSP tech mapping
   ↓
4. Extract with ILP → get DSP-mapped design
   ↓
5. Identify unmapped word-level cells
   ↓
6. 🆕 BITBLAST unmapped cells to AIG
   ↓
7. 🆕 Create AIG NetlistDB from bitblasted logic
   ↓
8. Apply LUT mapping (greedy mode)
   ↓
9. Final design: DSPs + LUTs + remaining logic
```

## Implementation Steps

### Step 1: Bitblasting Module

Create `emap/rewrites/bitblast.py`:

```python
def bitblast_aby_cell(netlist, cell_id, cell_type, a_vec, b_vec, y_vec):
    """Bitblast a word-level ABY cell to AIG."""
    if cell_type in ['$adds', '$addu']:
        return bitblast_add(a_vec, b_vec, y_vec)
    elif cell_type in ['$muls', '$mulu']:
        return bitblast_mul(a_vec, b_vec, y_vec)
    elif cell_type == '$mux':
        return bitblast_mux(a_vec, b_vec, s_vec, y_vec)
    # ... more cell types

def bitblast_extracted_design(extracted_mod, output_aig_db):
    """Convert extracted word-level design to AIG."""
    aig_db = create_aig_netlist_db()

    for cell_name, cell in extracted_mod['cells'].items():
        if cell['type'] in WORD_LEVEL_TYPES:
            bitblast_aby_cell(aig_db, cell_name, cell['type'],
                            cell['connections']['A'],
                            cell['connections']['B'],
                            cell['connections']['Y'])
        elif cell['type'] == '$dff':
            # DFFs stay as-is in AIG
            add_dff_to_aig(aig_db, cell)
        elif cell['type'] == 'dsp_generic':
            # DSPs stay as-is
            add_dsp_to_result(cell)

    return aig_db
```

### Step 2: Integration Script

```python
# test_dsp_lut_combined.py

# Run DSP mapping (word-level)
netlist_wordlevel = emap.NetlistDB('emap/schema.sql')
netlist_wordlevel.build_from_json(fir_data)
# ... apply rewrites, DSP mapping
extracted_mod = emap.extracts.ilp.extract_techmap_with_limit(...)

# Bitblast unmapped logic
netlist_aig = emap.rewrites.bitblast_extracted_design(extracted_mod)

# Apply LUT mapping on AIG
emap.rewrites.techmap_luts(netlist_aig, k=6, cnt=100, rseed=42, greedy=True)

# Combine DSPs + LUTs
final_design = combine_dsp_lut_designs(extracted_mod, netlist_aig)
```

### Step 3: Validation

Test on increasing complexity:
1. **Simple**: 4-bit adder (verify correctness)
2. **Medium**: FIR n=16 w=8 (measure QOR improvement)
3. **Large**: Systolic 4x4 w=8 (full pipeline)

## Expected Results

### FIR n=16 w=8 (Before)
- DSPs only: ~16-32 DSPs (for multipliers)
- Remaining: ~16 adders → mapped to fabric LUTs/logic

### FIR n=16 w=8 (After DSP+LUT)
- DSPs: ~16-32 DSPs
- Bitblasted adders: ~384 AND gates
- LUT-mapped: 50-100 6-LUTs (covers ~200-400 gates)
- **Benefit**: Fewer raw AND gates → better area/delay

## References

- LUT mapper: `emap/rewrites/lut.py`
- Word-level extraction: `emap/extracts/ilp.py`
- Test script: `test_dsp_plus_lut_mapping.py`
- Evaluation: `LUT_MAPPING_EVALUATION_RESULTS.md`

## Next Actions

1. ✅ Fixed FIR/systolic loading (word-level DB restored)
2. ✅ Confirmed DSP mapping + extraction works
3. ✅ Verified LUT mapping works on AIG
4. ⏭️ **Implement bitblasting module** ← Next step
5. ⏭️ Integrate bitblasting with DSP extraction
6. ⏭️ Evaluate DSPs+LUTs vs DSPs-only on FIR/systolic

## Dependencies

Fresh venv with:
```bash
python3 -m venv venv
source venv/bin/activate
pip install numpy scipy pulp
```

Tested on:
- Python 3.12
- numpy 2.3.3
- pulp 3.3.0
- scipy 1.16.2
