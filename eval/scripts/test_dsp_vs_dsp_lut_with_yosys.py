#!/usr/bin/env python3
"""
Compare DSP-only vs DSP+LUT with Yosys synthesis verification.

This properly compares by running Yosys synth_xilinx on both:
1. DSP-only: Extract DSPs, export to JSON, synthesize with Yosys
2. DSP+LUT: Extract DSPs, bitblast+LUT map, export to JSON, synthesize with Yosys
"""

import sys
import os
import json
import subprocess
import tempfile

sys.path.insert(0, os.path.abspath('.'))

import nextmap
from nextmap.rewrites import inplace_bitblast, aig_to_json

def simple_cost_model(type_: str, *ports) -> float:
    """Cost model for DSP extraction."""
    if type_ == "$dff":
        return len(ports[0]) * 1.0
    elif type_ in {"$muls", "$mulu"}:
        return len(ports[0]) * len(ports[1]) * 1.0
    elif type_ in {"$adds", "$addu", "$subs", "$subu"}:
        return min(len(ports[0]) + len(ports[1]), len(ports[2])) * 1.0
    return len(ports[0]) * 1.0

def synthesize_with_yosys(json_path: str, design_name: str):
    """
    Run Yosys synth_xilinx and extract resource counts.

    Returns: dict with LUTs, DFFs, DSPs counts
    """
    stat_file = f"/tmp/{design_name}_stat.txt"

    # Create DSP blackbox
    dsp_blackbox = tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False)
    dsp_blackbox.write("""
module dsp_generic(
    input clk,
    input [47:0] inputs,
    output [47:0] outputs
);
endmodule
""")
    dsp_blackbox.close()

    yosys_cmd = f"yosys -q -p \"read_json {json_path}; read_verilog {dsp_blackbox.name}; synth_xilinx -family xcup -noiopad; tee -o {stat_file} stat\""

    try:
        result = subprocess.run(yosys_cmd, shell=True, check=True, capture_output=True, text=True)

        # Parse stat output
        with open(stat_file, 'r') as f:
            stat_output = f.read()

        # Extract counts (matching paper format: DSP, CARRY4, FF, Other)
        luts = 0
        carry4 = 0
        ff = 0
        dsps = 0
        muxfx = 0

        for line in stat_output.split('\n'):
            # Match lines like "       92   LUT2" or "       25   LUT3"
            if 'LUT' in line:
                parts = line.split()
                # Look for pattern: number followed by LUTx
                for i, part in enumerate(parts):
                    if part.startswith('LUT') and i > 0:
                        # Previous part should be the count
                        if parts[i-1].isdigit():
                            luts += int(parts[i-1])
            elif 'CARRY4' in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == 'CARRY4' and i > 0:
                        if parts[i-1].isdigit():
                            carry4 += int(parts[i-1])
            elif 'MUXF7' in line or 'MUXF8' in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part.startswith('MUXF') and i > 0:
                        if parts[i-1].isdigit():
                            muxfx += int(parts[i-1])
            elif 'FDRE' in line or 'FDCE' in line or 'FDPE' in line or 'FDSE' in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part.isdigit():
                        ff += int(part)
            elif 'DSP48E2' in line:
                parts = line.split()
                # Look for pattern: number followed by DSP48E2
                for i, part in enumerate(parts):
                    if part == 'DSP48E2' and i > 0:
                        if parts[i-1].isdigit():
                            dsps += int(parts[i-1])

        os.unlink(dsp_blackbox.name)

        return {
            'dsp': dsps,
            'carry4': carry4,
            'ff': ff,
            'luts': luts,
            'muxfx': muxfx,
            'success': True
        }

    except subprocess.CalledProcessError as e:
        print(f"  ❌ Yosys failed: {e.stderr}")
        return {
            'dsp': 0,
            'carry4': 0,
            'ff': 0,
            'luts': 0,
            'muxfx': 0,
            'success': False
        }

def run_dsp_only_with_yosys(benchmark_path: str, dsp_limit: int = 64):
    """Run DSP extraction and synthesize with Yosys."""
    design_name = os.path.basename(benchmark_path).replace('.json', '')
    print(f"\n{'='*80}")
    print(f"DSP-ONLY: {design_name}")
    print(f"{'='*80}")

    # Extract DSPs
    netlist = nextmap.NetlistDB('nextmap/schema.sql')
    with open(benchmark_path) as f:
        netlist.build_from_json(json.load(f)['modules']['top'])

    netlist.rebuild()
    for iteration in range(10):
        comm_matches = nextmap.rewrites.ematch_comm(netlist, ["$adds", "$muls"])
        dff_fwd_matches = nextmap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$adds", "$muls"])
        dff_bwd_matches = nextmap.rewrites.ematch_dff_backward_aby_cell(netlist, ["$adds", "$muls"])

        cnt = nextmap.rewrites.apply_comm(netlist, comm_matches)
        cnt += nextmap.rewrites.apply_dff_forward_aby_cell(netlist, dff_fwd_matches)
        cnt += nextmap.rewrites.apply_dff_backward_aby_cell(netlist, dff_bwd_matches)

        if cnt > 0:
            netlist.rebuild()
        else:
            break

    nextmap.rewrites.rewrite_sdff(netlist)

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

    extracted_mod = nextmap.extracts.ilp.extract_techmap_with_limit(
        netlist,
        simple_cost_model,
        dsp_rules,
        {"dsp48e2": dsp_limit},
        solver_type='cbc',
        OutputFlag=False
    )

    # Export to JSON
    json_path = f"/tmp/{design_name}_dsp_only.json"
    with open(json_path, 'w') as f:
        json.dump({'modules': {'top': extracted_mod}}, f, indent=2)

    print(f"  Exported to {json_path}")
    print(f"  Running Yosys synthesis...")

    # Synthesize with Yosys
    yosys_result = synthesize_with_yosys(json_path, f"{design_name}_dsp_only")

    print(f"  ✅ Yosys synthesis results:")
    print(f"    DSP: {yosys_result['dsp']}")
    print(f"    CARRY4: {yosys_result['carry4']}")
    print(f"    FF: {yosys_result['ff']}")
    print(f"    LUTs: {yosys_result['luts']}")
    if yosys_result['muxfx'] > 0:
        print(f"    MUXFx: {yosys_result['muxfx']}")

    return yosys_result

def run_dsp_plus_lut_with_yosys(benchmark_path: str, lut_count: int, dsp_limit: int = 64):
    """Run DSP extraction + LUT mapping and synthesize with Yosys."""
    design_name = os.path.basename(benchmark_path).replace('.json', '')
    print(f"\n{'='*80}")
    print(f"DSP+LUT: {design_name}")
    print(f"{'='*80}")

    # Extract DSPs (same as above)
    netlist = nextmap.NetlistDB('nextmap/schema.sql')
    with open(benchmark_path) as f:
        netlist.build_from_json(json.load(f)['modules']['top'])

    netlist.rebuild()
    for iteration in range(10):
        comm_matches = nextmap.rewrites.ematch_comm(netlist, ["$adds", "$muls"])
        dff_fwd_matches = nextmap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$adds", "$muls"])
        dff_bwd_matches = nextmap.rewrites.ematch_dff_backward_aby_cell(netlist, ["$adds", "$muls"])

        cnt = nextmap.rewrites.apply_comm(netlist, comm_matches)
        cnt += nextmap.rewrites.apply_dff_forward_aby_cell(netlist, dff_fwd_matches)
        cnt += nextmap.rewrites.apply_dff_backward_aby_cell(netlist, dff_bwd_matches)

        if cnt > 0:
            netlist.rebuild()
        else:
            break

    nextmap.rewrites.rewrite_sdff(netlist)

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

    extracted_mod = nextmap.extracts.ilp.extract_techmap_with_limit(
        netlist,
        simple_cost_model,
        dsp_rules,
        {"dsp48e2": dsp_limit},
        solver_type='cbc',
        OutputFlag=False
    )

    # Now bitblast and LUT map
    print(f"  Bitblasting unmapped logic...")
    netlist_lut, bitblaster, wire_mapping = inplace_bitblast.bitblast_extracted_design_inplace(
        extracted_mod,
        schema_file="nextmap/schema.sql"
    )

    stats = inplace_bitblast.optimize_and_map_luts(
        netlist_lut,
        bitblaster,
        k=6,
        lut_count=lut_count,
        rseed=42
    )

    print(f"  Mapped {stats['luts_created']} LUTs")

    # Export to JSON
    print(f"  Exporting to JSON...")
    json_module, renumber_map = aig_to_json.aig_to_json_module(netlist_lut, wire_mapping)

    # Copy ports from extracted design (critical - without ports, design is dead logic!)
    json_module['ports'] = extracted_mod.get('ports', {})

    # Helper function to remap wires using bitblast mapping
    def remap_wire_list(wires):
        """Remap a list of wires using the bitblast wire mapping."""
        result = []
        for wire in wires:
            if wire in wire_mapping:
                # This wire was an output of a bitblasted cell - use the new wire(s)
                new_wires = wire_mapping[wire]
                # For now, take the first if it's a list
                if isinstance(new_wires, list) and len(new_wires) > 0:
                    result.append(new_wires[0])
                else:
                    result.append(new_wires)
            else:
                # Wire not remapped, keep as-is
                result.append(wire)
        return result

    # Add DSP and DFF cells back to the module, with remapped connections
    cells = extracted_mod.get('cells', {})
    for cell_name, cell_info in cells.items():
        if 'dsp' in cell_info.get('type', '').lower() or 'mul' in cell_info.get('type', '').lower():
            json_module['cells'][cell_name] = cell_info
        elif cell_info.get('type') == '$dff':
            # Add DFFs with remapped connections
            dff_copy = dict(cell_info)
            if 'connections' in dff_copy:
                remapped_conns = {}
                for port, wires in dff_copy['connections'].items():
                    if port == 'CLK':
                        # Don't remap clock
                        remapped_conns[port] = wires
                    else:
                        # Remap other connections (D inputs might be from bitblasted cells)
                        remapped_conns[port] = remap_wire_list(wires)
                dff_copy['connections'] = remapped_conns
            json_module['cells'][cell_name] = dff_copy

    json_path = f"/tmp/{design_name}_dsp_lut.json"
    with open(json_path, 'w') as f:
        json.dump({'modules': {'top': json_module}}, f, indent=2)

    print(f"  Exported to {json_path}")
    print(f"  Running Yosys synthesis...")

    # Synthesize with Yosys
    yosys_result = synthesize_with_yosys(json_path, f"{design_name}_dsp_lut")

    print(f"  ✅ Yosys synthesis results:")
    print(f"    DSP: {yosys_result['dsp']}")
    print(f"    CARRY4: {yosys_result['carry4']}")
    print(f"    FF: {yosys_result['ff']}")
    print(f"    LUTs: {yosys_result['luts']}")
    if yosys_result['muxfx'] > 0:
        print(f"    MUXFx: {yosys_result['muxfx']}")

    return yosys_result

def main():
    """Run comparison with Yosys synthesis."""
    print("🔬 DSP-ONLY vs DSP+LUT COMPARISON (With Yosys Synthesis)")
    print("="*80)

    tests = [
        ('eval/out/fir_n16_w8.json', 100, 32),
        ('eval/out/fir_n16_w16.json', 150, 32),
        ('eval/out/systolic_matmul_4x4_w8.json', 200, 32),
    ]

    results = []

    for design_path, lut_cnt, dsp_lim in tests:
        if not os.path.exists(design_path):
            print(f"\n⚠️  Skipping {design_path} (not found)")
            continue

        design_name = os.path.basename(design_path).replace('.json', '')

        try:
            print(f"\n{'#'*80}")
            print(f"# {design_name}")
            print(f"{'#'*80}")

            dsp_only = run_dsp_only_with_yosys(design_path, dsp_lim)
            dsp_lut = run_dsp_plus_lut_with_yosys(design_path, lut_cnt, dsp_lim)

            results.append({
                'design': design_name,
                'dsp_only': dsp_only,
                'dsp_lut': dsp_lut
            })

        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

    # Summary
    if results:
        print(f"\n{'='*80}")
        print("📊 FINAL SUMMARY (Yosys synth_xilinx results)")
        print(f"{'='*80}")
        print(f"\n{'Design':<25} {'Approach':<12} {'DSP':>6} {'CARRY4':>8} {'FF':>6} {'Other':>20}")
        print("-"*80)
        for r in results:
            # Format Other column with LUTx and MUXFx if present
            dsp_only_other = []
            if r['dsp_only']['luts'] > 0:
                dsp_only_other.append(f"LUTx: {r['dsp_only']['luts']}")
            if r['dsp_only'].get('muxfx', 0) > 0:
                dsp_only_other.append(f"MUXFx: {r['dsp_only']['muxfx']}")
            dsp_only_str = ', '.join(dsp_only_other) if dsp_only_other else "N.A."

            dsp_lut_other = []
            if r['dsp_lut']['luts'] > 0:
                dsp_lut_other.append(f"LUTx: {r['dsp_lut']['luts']}")
            if r['dsp_lut'].get('muxfx', 0) > 0:
                dsp_lut_other.append(f"MUXFx: {r['dsp_lut']['muxfx']}")
            dsp_lut_str = ', '.join(dsp_lut_other) if dsp_lut_other else "N.A."

            print(f"{r['design']:<25} {'DSP-only':<12} {r['dsp_only']['dsp']:>6} {r['dsp_only']['carry4']:>8} {r['dsp_only']['ff']:>6} {dsp_only_str:>20}")
            print(f"{'':<25} {'DSP+LUT':<12} {r['dsp_lut']['dsp']:>6} {r['dsp_lut']['carry4']:>8} {r['dsp_lut']['ff']:>6} {dsp_lut_str:>20}")
            print()

        # Comparison with paper results (Table 5: tab:large_comparison)
        paper_results = {
            'systolic_matmul_4x4_w8': {
                'nextmap': {'dsp': 16, 'carry4': 0, 'ff': 128, 'luts': 0},
                'yosys': {'dsp': 16, 'carry4': 64, 'ff': 448, 'luts': 400},
                'proprietary': {'dsp': 0, 'carry4': 256, 'ff': 448, 'luts': 1648}
            },
            'systolic_matmul_4x4_w16': {
                'nextmap': {'dsp': 16, 'carry4': 0, 'ff': 256, 'luts': 0},
                'yosys': {'dsp': 16, 'carry4': 128, 'ff': 896, 'luts': 864},
                'proprietary': {'dsp': 16, 'carry4': 0, 'ff': 16, 'luts': 0}
            }
        }

        print(f"\n{'='*80}")
        print("📊 COMPARISON WITH PAPER RESULTS (Table 5)")
        print(f"{'='*80}")

        for r in results:
            design = r['design']
            if design in paper_results:
                print(f"\n{design}:")
                print(f"  {'Tool':<20} {'DSP':>6} {'CARRY4':>8} {'FF':>6} {'LUTs':>8}")
                print(f"  {'-'*50}")

                paper = paper_results[design]
                print(f"  {'Nextmap (paper)':<20} {paper['nextmap']['dsp']:>6} {paper['nextmap']['carry4']:>8} {paper['nextmap']['ff']:>6} {paper['nextmap']['luts']:>8}")
                print(f"  {'Yosys (paper)':<20} {paper['yosys']['dsp']:>6} {paper['yosys']['carry4']:>8} {paper['yosys']['ff']:>6} {paper['yosys']['luts']:>8}")
                print(f"  {'Proprietary (paper)':<20} {paper['proprietary']['dsp']:>6} {paper['proprietary']['carry4']:>8} {paper['proprietary']['ff']:>6} {paper['proprietary']['luts']:>8}")
                print(f"  {'-'*50}")
                print(f"  {'DSP-only (ours)':<20} {r['dsp_only']['dsp']:>6} {r['dsp_only']['carry4']:>8} {r['dsp_only']['ff']:>6} {r['dsp_only']['luts']:>8}")
                print(f"  {'DSP+LUT (ours)':<20} {r['dsp_lut']['dsp']:>6} {r['dsp_lut']['carry4']:>8} {r['dsp_lut']['ff']:>6} {r['dsp_lut']['luts']:>8}")

        # Save results
        with open('dsp_vs_dsp_lut_yosys.json', 'w') as f:
            json.dump(results, f, indent=2)

        print(f"💾 Results saved to: dsp_vs_dsp_lut_yosys.json")

if __name__ == "__main__":
    main()
