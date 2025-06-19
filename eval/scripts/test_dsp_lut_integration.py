#!/usr/bin/env python3
"""
Full DSP + LUT mapping integration test.

Workflow:
1. Load systolic benchmark (word-level)
2. Apply equality saturation
3. Apply DSP tech mapping
4. Extract with ILP
5. Bitblast unmapped cells to AIG
6. Apply LUT mapping
7. Compare results
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.abspath('.'))

import nextmap
from nextmap.rewrites import bitblast_to_aig

def simple_cost_model(type_: str, *ports) -> float:
    """Cost model for DSP extraction."""
    if type_ == "$dff":
        return len(ports[0]) * 1.0
    elif type_ in {"$muls", "$mulu"}:
        return len(ports[0]) * len(ports[1]) * 1.0
    elif type_ in {"$adds", "$addu", "$subs", "$subu"}:
        return min(len(ports[0]) + len(ports[1]), len(ports[2])) * 1.0
    return len(ports[0]) * 1.0


def run_full_pipeline(benchmark_path: str, lut_count: int = 100):
    """Run the complete DSP + LUT mapping pipeline."""

    print("="*80)
    print("FULL DSP + LUT MAPPING PIPELINE")
    print("="*80)

    # ========================================================================
    # STEP 1: Load and optimize (word-level)
    # ========================================================================
    print("\n📂 STEP 1: Load benchmark (word-level)")
    print("-"*80)

    netlist = nextmap.NetlistDB('nextmap/schema.sql')
    with open(benchmark_path) as f:
        netlist.build_from_json(json.load(f)['modules']['top'])

    print(f"Loaded: {benchmark_path}")

    # ========================================================================
    # STEP 2: Apply rewrites
    # ========================================================================
    print("\n🔄 STEP 2: Apply equality saturation")
    print("-"*80)

    netlist.rebuild()

    rewrite_count = 0
    for iteration in range(10):
        comm_matches = nextmap.rewrites.ematch_comm(netlist, ["$adds", "$muls"])
        dff_fwd_matches = nextmap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$adds", "$muls"])
        dff_bwd_matches = nextmap.rewrites.ematch_dff_backward_aby_cell(netlist, ["$adds", "$muls"])

        cnt = 0
        cnt += nextmap.rewrites.apply_comm(netlist, comm_matches)
        cnt += nextmap.rewrites.apply_dff_forward_aby_cell(netlist, dff_fwd_matches)
        cnt += nextmap.rewrites.apply_dff_backward_aby_cell(netlist, dff_bwd_matches)

        rewrite_count += cnt
        if cnt > 0:
            print(f"  Iteration {iteration}: {cnt} rewrites")
            netlist.rebuild()
        else:
            print(f"  ✅ Converged after {iteration} iterations")
            break

    # SDFF rewrites
    sdff_cnt = nextmap.rewrites.rewrite_sdff(netlist)
    print(f"  ✅ Applied {sdff_cnt} SDFF rewrites")

    # ========================================================================
    # STEP 3: DSP tech mapping
    # ========================================================================
    print("\n🎯 STEP 3: DSP tech mapping")
    print("-"*80)

    dsp_rules = {
        "dsp_generic": {
            "requirements": {"dsp48e2": 1},
            "hidden_inputs": ["clk"],
            "inputs": ["inputs"],
            "outputs": ["outputs"]
        }
    }

    nextmap.rewrites.create_tech_tables(netlist, dsp_rules)
    nextmap.rewrites.rewrite_tech(netlist, dsp_rules)
    print("  ✅ DSP tech mapping completed")

    # ========================================================================
    # STEP 4: Extract with ILP
    # ========================================================================
    print("\n🔍 STEP 4: ILP extraction")
    print("-"*80)

    extracted_mod = nextmap.extracts.ilp.extract_techmap_with_limit(
        netlist,
        simple_cost_model,
        dsp_rules,
        {"dsp48e2": 64},
        solver_type='cbc',
        OutputFlag=False
    )

    cells = extracted_mod.get('cells', {})
    dsp_cells = sum(1 for cell in cells.values() if cell.get('type') == 'dsp_generic')

    print(f"  ✅ Extracted {len(cells)} cells")
    print(f"  DSP cells: {dsp_cells}")
    print(f"  Other cells: {len(cells) - dsp_cells}")

    # ========================================================================
    # STEP 5: Bitblast unmapped logic
    # ========================================================================
    print("\n⚙️  STEP 5: Bitblast unmapped cells to AIG")
    print("-"*80)

    bitblast_start = time.time()
    aig_db = bitblast_to_aig.bitblast_extracted_design(extracted_mod)
    bitblast_time = time.time() - bitblast_start

    print(f"  ✅ Bitblasting completed in {bitblast_time:.3f}s")

    # Analyze bitblasted design
    stats = bitblast_to_aig.analyze_bitblasted_design(aig_db)

    # ========================================================================
    # STEP 6: Apply LUT mapping
    # ========================================================================
    print("\n🎲 STEP 6: LUT mapping (greedy mode)")
    print("-"*80)

    lut_start = time.time()

    # Apply LUT mapping
    from nextmap.rewrites import lut
    lut.techmap_luts(aig_db, k=6, cnt=lut_count, rseed=42, greedy=True)

    lut_time = time.time() - lut_start

    # Count LUTs created
    cur = aig_db.execute("SELECT COUNT(*) FROM luts")
    lut_cnt = cur.fetchone()[0]

    print(f"  ✅ LUT mapping completed in {lut_time:.3f}s")
    print(f"  LUTs created: {lut_cnt}")

    # ========================================================================
    # STEP 7: Final Summary
    # ========================================================================
    print("\n" + "="*80)
    print("📊 FINAL RESULTS")
    print("="*80)

    print(f"\n🎯 DSP Mapping:")
    print(f"  DSP cells: {dsp_cells}")

    print(f"\n⚙️  Bitblasting:")
    print(f"  AND gates: {stats['and_gates']}")
    print(f"  INV gates: {stats['inv_gates']}")
    print(f"  Total gates: {stats['total_gates']}")

    print(f"\n🎲 LUT Mapping:")
    print(f"  LUTs created: {lut_cnt}")
    print(f"  Gates covered: ~{lut_cnt * 5} (estimate)")
    print(f"  Remaining gates: ~{stats['total_gates'] - lut_cnt * 5}")

    print(f"\n⏱️  Performance:")
    print(f"  Bitblasting: {bitblast_time:.3f}s")
    print(f"  LUT mapping: {lut_time:.3f}s")
    print(f"  Total: {bitblast_time + lut_time:.3f}s")

    return {
        'dsp_cells': dsp_cells,
        'bitblast_stats': stats,
        'luts_created': lut_cnt,
        'bitblast_time': bitblast_time,
        'lut_time': lut_time
    }


def main():
    """Run the integration test."""
    print("🚀 DSP + LUT MAPPING INTEGRATION TEST")
    print("="*80)

    benchmark = 'eval/out/systolic_matmul_4x4_w8.json'

    try:
        results = run_full_pipeline(benchmark, lut_count=100)

        print("\n✅ SUCCESS! Full pipeline completed.")
        print("\n💡 Next steps:")
        print("  - Test on larger benchmarks (8x8, FIR)")
        print("  - Compare against DSP-only baseline")
        print("  - Measure actual area/delay improvements")

        # Save results
        with open('dsp_lut_integration_results.json', 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n💾 Results saved to: dsp_lut_integration_results.json")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
