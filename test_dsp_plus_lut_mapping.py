#!/usr/bin/env python3
"""
Test DSP mapping followed by LUT mapping on unmapped logic.

Workflow:
1. Load systolic benchmark (word-level)
2. Apply equality saturation + DSP tech mapping
3. Extract with ILP solver -> get DSP-mapped design
4. Identify unmapped logic (cells not covered by DSPs)
5. Bitblast unmapped logic to AIG
6. Apply LUT mapping on the AIG
7. Compare: DSPs-only vs DSPs+LUTs
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.abspath('.'))

import emap

def simple_cost_model(type_: str, *ports) -> float:
    """Cost model for DSP extraction."""
    if type_ == "$dff":
        return len(ports[0]) * 1.0
    elif type_ in {"$muls", "$mulu"}:
        return len(ports[0]) * len(ports[1]) * 1.0
    elif type_ in {"$adds", "$addu", "$subs", "$subu"}:
        return min(len(ports[0]) + len(ports[1]), len(ports[2])) * 1.0
    return len(ports[0]) * 1.0

def run_dsp_mapping_only(benchmark_path: str):
    """Run DSP mapping without LUT optimization."""
    print("="*70)
    print("STEP 1: DSP MAPPING ONLY (Baseline)")
    print("="*70)

    netlist = emap.NetlistDB('emap/schema.sql')
    with open(benchmark_path) as f:
        netlist.build_from_json(json.load(f)['modules']['top'])

    print(f"Loaded: {benchmark_path}")

    # Apply rewrites
    print("\nApplying rewrites...")
    netlist.rebuild()

    rewrite_count = 0
    for iteration in range(10):
        comm_matches = emap.rewrites.ematch_comm(netlist, ["$adds", "$muls"])
        dff_fwd_matches = emap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$adds", "$muls"])
        dff_bwd_matches = emap.rewrites.ematch_dff_backward_aby_cell(netlist, ["$adds", "$muls"])

        cnt = 0
        cnt += emap.rewrites.apply_comm(netlist, comm_matches)
        cnt += emap.rewrites.apply_dff_forward_aby_cell(netlist, dff_fwd_matches)
        cnt += emap.rewrites.apply_dff_backward_aby_cell(netlist, dff_bwd_matches)

        rewrite_count += cnt
        if cnt > 0:
            print(f"  Iteration {iteration}: {cnt} rewrites")
            netlist.rebuild()
        else:
            print(f"  Converged after {iteration} iterations")
            break

    # SDFF rewrites
    print("\nApplying SDFF rewrites...")
    sdff_cnt = emap.rewrites.rewrite_sdff(netlist)
    print(f"  Applied {sdff_cnt} SDFF rewrites")

    # DSP tech mapping
    print("\nCreating tech mapping tables...")
    dsp_rules = {
        "dsp_generic": {
            "requirements": {"dsp48e2": 1},
            "hidden_inputs": ["clk"],
            "inputs": ["inputs"],
            "outputs": ["outputs"]
        }
    }

    emap.rewrites.create_tech_tables(netlist, dsp_rules)
    print("Applying DSP tech mapping...")
    emap.rewrites.rewrite_tech(netlist, dsp_rules)

    # Extract with ILP
    print("\nExtracting with CBC solver...")
    mod = emap.extracts.ilp.extract_techmap_with_limit(
        netlist,
        simple_cost_model,
        dsp_rules,
        {"dsp48e2": 64},
        solver_type='cbc',
        OutputFlag=False
    )

    # Analyze results
    cells = mod.get('cells', {})
    total_cells = len(cells)
    dsp_cells = sum(1 for cell in cells.values() if cell.get('type') == 'dsp_generic')
    other_cells = total_cells - dsp_cells

    # Count cell types
    cell_types = {}
    for cell in cells.values():
        t = cell.get('type', 'unknown')
        cell_types[t] = cell_types.get(t, 0) + 1

    print(f"\n✅ DSP-Only Results:")
    print(f"  Total cells: {total_cells}")
    print(f"  DSP cells: {dsp_cells}")
    print(f"  Other cells: {other_cells}")
    print(f"  Cell type breakdown:")
    for t, count in sorted(cell_types.items()):
        print(f"    {t}: {count}")

    return {
        'total_cells': total_cells,
        'dsp_cells': dsp_cells,
        'other_cells': other_cells,
        'cell_types': cell_types,
        'mod': mod
    }

def analyze_unmapped_logic(mod: dict):
    """Analyze what logic didn't get mapped to DSPs."""
    print("\n" + "="*70)
    print("STEP 2: ANALYZE UNMAPPED LOGIC")
    print("="*70)

    cells = mod.get('cells', {})
    unmapped_cells = {
        name: cell for name, cell in cells.items()
        if cell.get('type') != 'dsp_generic'
    }

    print(f"\nUnmapped cells: {len(unmapped_cells)}")

    # Check what types of unmapped cells we have
    unmapped_types = {}
    for cell in unmapped_cells.values():
        t = cell.get('type', 'unknown')
        unmapped_types[t] = unmapped_types.get(t, 0) + 1

    print("Unmapped cell types:")
    for t, count in sorted(unmapped_types.items()):
        print(f"  {t}: {count}")

    # Check if we have bit-level gates that could benefit from LUT mapping
    bit_level_types = {'$and', '$or', '$xor', '$not', '$_AND_', '$_OR_', '$_XOR_', '$_NOT_'}
    bit_level_cells = sum(
        count for t, count in unmapped_types.items()
        if t in bit_level_types
    )

    print(f"\nBit-level gates (LUT-mappable): {bit_level_cells}")

    if bit_level_cells > 0:
        print("✅ Found bit-level gates - LUT mapping could help!")
    else:
        print("⚠️  No bit-level gates found - might need bitblasting first")

    return unmapped_cells, unmapped_types

def main():
    """Run the DSP + LUT mapping experiment."""
    print("🔬 DSP + LUT MAPPING EXPERIMENT")
    print("=" * 70)

    # Test on systolic 4x4 (smaller, faster)
    benchmark = 'eval/out/systolic_matmul_4x4_w8.json'

    # Step 1: DSP mapping only
    dsp_result = run_dsp_mapping_only(benchmark)

    # Step 2: Analyze unmapped logic
    unmapped_cells, unmapped_types = analyze_unmapped_logic(dsp_result['mod'])

    # Step 3: Check if we can do LUT mapping
    print("\n" + "="*70)
    print("STEP 3: LUT MAPPING POTENTIAL")
    print("="*70)

    # The unmapped logic is still in word-level format
    # We'd need to:
    # 1. Bitblast the unmapped cells to AIG
    # 2. Create a new NetlistDB with AIG schema
    # 3. Apply LUT mapping

    print("\n💡 Next Steps Needed:")
    print("  1. Implement bitblasting for unmapped word-level cells")
    print("  2. Create AIG NetlistDB from bitblasted logic")
    print("  3. Apply LUT mapping to AIG")
    print("  4. Combine DSPs + LUTs in final netlist")

    print(f"\n📊 Summary:")
    print(f"  Benchmark: {benchmark}")
    print(f"  DSP cells: {dsp_result['dsp_cells']}")
    print(f"  Unmapped cells: {dsp_result['other_cells']}")
    print(f"  Potential for LUT optimization: {len(unmapped_cells)} cells")

    # Save results
    with open('dsp_lut_analysis.json', 'w') as f:
        json.dump({
            'benchmark': benchmark,
            'dsp_result': {
                'total_cells': dsp_result['total_cells'],
                'dsp_cells': dsp_result['dsp_cells'],
                'other_cells': dsp_result['other_cells'],
                'cell_types': dsp_result['cell_types']
            },
            'unmapped_types': unmapped_types
        }, f, indent=2)

    print("\n💾 Analysis saved to: dsp_lut_analysis.json")

if __name__ == "__main__":
    main()
