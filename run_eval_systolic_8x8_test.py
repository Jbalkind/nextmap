#!/usr/bin/env python3
"""
Run the exact systolic_matmul_8x8_w8 test from eval_systolic_v2.ipynb
with both Gurobi and CBC to compare behavior
"""

import sys
import os
import json
import time
sys.path.insert(0, os.path.abspath('.'))

import emap

def simple_cost_model(type_: str, *ports) -> float:
    """Cost model from eval_systolic_v2.ipynb"""
    if type_ == "$dff":
        return len(ports[0]) * 1.0
    elif type_ in {"$muls", "$mulu"}:
        return len(ports[0]) * len(ports[1]) * 1.0
    elif type_ in {"$adds", "$addu", "$subs", "$subu"}:
        return min(len(ports[0]) + len(ports[1]), len(ports[2])) * 1.0
    return len(ports[0]) * 1.0  # other types

def run_8x8_test_with_solver(solver_type):
    """Run the exact 8x8 test from the notebook with specified solver"""

    print(f"\n{'='*60}")
    print(f"RUNNING 8x8 TEST WITH {solver_type.upper()}")
    print(f"{'='*60}")

    # DSP rules from notebook
    dsp_rules = {
        "dsp_generic": {
            "requirements": {
                "dsp48e2": 1
            },
            "hidden_inputs": ["clk"],
            "inputs": ["inputs"],
            "outputs": ["outputs"]
        }
    }

    try:
        start_time = time.time()

        # Exact code from notebook cell c8c7b9f1
        SCHEMA_PATH = "emap/schema.sql"
        TEST_NAME = "systolic_matmul_8x8_w8"

        print(f"Loading {TEST_NAME}...")
        netlist = emap.NetlistDB(SCHEMA_PATH)
        with open(f"eval/out/{TEST_NAME}.json", "r") as f:
            netlist.build_from_json(json.load(f)["modules"]["top"])

        netlist.rebuild()

        # Apply rewrites exactly as in notebook
        print("Applying rewrites...")
        rewrite_count = 0
        cnt = 1
        while cnt > 0:
            comm_matches = emap.rewrites.ematch_comm(netlist, ["$adds", "$muls"])
            dff_forward_aby_cell_matches = emap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$adds", "$muls"])
            dff_backward_aby_cell_matches = emap.rewrites.ematch_dff_backward_aby_cell(netlist, ["$adds", "$muls"])

            cnt = 0
            cnt += emap.rewrites.apply_comm(netlist, comm_matches)
            cnt += emap.rewrites.apply_dff_forward_aby_cell(netlist, dff_forward_aby_cell_matches)
            cnt += emap.rewrites.apply_dff_backward_aby_cell(netlist, dff_backward_aby_cell_matches)

            if cnt > 0:
                print(f"Applied {cnt} rewrites")
                rewrite_count += cnt
            else:
                print("No rewrites applied, stopping")
            netlist.rebuild()

        # SDFF rewrites (specific to 8x8)
        print("Applying SDFF rewrites...")
        sdff_cnt = emap.rewrites.rewrite_sdff(netlist)
        print(f"Applied {sdff_cnt} SDFF rewrites")

        # Tech mapping - NOTE: notebook uses rewrite_tech not techmap_dsp!
        print("Creating tech tables...")
        emap.rewrites.create_tech_tables(netlist, dsp_rules)
        print("Applying tech mapping...")
        emap.rewrites.rewrite_tech(netlist, dsp_rules)  # This is what notebook uses!

        # Extract with ILP
        print(f"Extracting with {solver_type} solver...")
        extract_start = time.time()

        mod = emap.extracts.ilp.extract_techmap_with_limit(
            netlist,
            simple_cost_model,
            dsp_rules,
            {"dsp48e2": 64},
            solver_type=solver_type,
            OutputFlag=False
        )

        extract_time = time.time() - extract_start
        total_time = time.time() - start_time

        # Analyze results
        cells = mod.get('cells', {})
        total_cells = len(cells)
        dsp_cells = sum(1 for cell in cells.values() if cell.get('type') == 'dsp_generic')

        print(f"\n✅ {solver_type.upper()} SUCCESS!")
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Extraction time: {extract_time:.2f}s")
        print(f"  Rewrites applied: {rewrite_count}")
        print(f"  SDFF rewrites: {sdff_cnt}")
        print(f"  Total cells: {total_cells}")
        print(f"  DSP cells: {dsp_cells}")
        print(f"  DSP utilization: {dsp_cells}/64 ({dsp_cells/64*100:.1f}%)")

        # Save result
        output_file = f"eval/out/{TEST_NAME}_{solver_type}_extracted.json"
        with open(output_file, "w") as f:
            json.dump({"creator": "nextmap", "modules": {"top": mod}}, f, indent=2)
        print(f"  Result saved to: {output_file}")

        return {
            'success': True,
            'solver': solver_type,
            'total_time': total_time,
            'extract_time': extract_time,
            'rewrites': rewrite_count,
            'sdff_rewrites': sdff_cnt,
            'total_cells': total_cells,
            'dsp_cells': dsp_cells,
            'dsp_utilization': dsp_cells/64
        }

    except Exception as e:
        print(f"\n❌ {solver_type.upper()} FAILED!")
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()

        return {
            'success': False,
            'solver': solver_type,
            'error': str(e)
        }

def main():
    """Run the comparison"""

    print("🔍 RUNNING EVAL_SYSTOLIC_V2 8x8 TEST WITH BOTH SOLVERS")
    print("Using exact code from notebook cell c8c7b9f1")

    # Test both solvers
    results = []

    for solver in ['gurobi', 'cbc']:
        result = run_8x8_test_with_solver(solver)
        results.append(result)

    # Compare results
    print(f"\n{'='*60}")
    print("COMPARISON SUMMARY")
    print(f"{'='*60}")

    gurobi_result = results[0]
    cbc_result = results[1]

    print(f"Gurobi: {'✅ SUCCESS' if gurobi_result['success'] else '❌ FAILED'}")
    if gurobi_result['success']:
        print(f"  Time: {gurobi_result['total_time']:.2f}s")
        print(f"  DSPs: {gurobi_result['dsp_cells']}/64 ({gurobi_result['dsp_utilization']*100:.1f}%)")
        print(f"  Total cells: {gurobi_result['total_cells']}")
    else:
        print(f"  Error: {gurobi_result['error']}")

    print(f"\nCBC: {'✅ SUCCESS' if cbc_result['success'] else '❌ FAILED'}")
    if cbc_result['success']:
        print(f"  Time: {cbc_result['total_time']:.2f}s")
        print(f"  DSPs: {cbc_result['dsp_cells']}/64 ({cbc_result['dsp_utilization']*100:.1f}%)")
        print(f"  Total cells: {cbc_result['total_cells']}")
    else:
        print(f"  Error: {cbc_result['error']}")

    # Analysis
    print(f"\n🔍 ANALYSIS:")

    if gurobi_result['success'] and cbc_result['success']:
        print("✅ Both solvers succeeded!")
        if gurobi_result['dsp_cells'] == cbc_result['dsp_cells']:
            print(f"✅ Identical DSP usage: {gurobi_result['dsp_cells']} DSPs")
        else:
            print(f"⚠️ Different DSP usage: Gurobi={gurobi_result['dsp_cells']}, CBC={cbc_result['dsp_cells']}")

    elif gurobi_result['success'] and not cbc_result['success']:
        print("🎯 Gurobi succeeds, CBC fails - this matches our previous observation")
        print(f"   Gurobi finds solution with {gurobi_result['dsp_cells']} DSPs")

    elif not gurobi_result['success'] and cbc_result['success']:
        print("🤔 CBC succeeds, Gurobi fails - unexpected!")

    else:
        print("❌ Both solvers failed")

    # Save detailed comparison
    with open('8x8_solver_comparison.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 Detailed comparison saved to: 8x8_solver_comparison.json")

if __name__ == "__main__":
    main()