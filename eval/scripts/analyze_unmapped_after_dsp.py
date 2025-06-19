#!/usr/bin/env python3
"""
Analyze what cells remain unmapped after DSP tech mapping.

This shows what could potentially benefit from LUT mapping.
"""

import sys
import os
import json

sys.path.insert(0, os.path.abspath('.'))

import nextmap

def analyze_post_dsp_mapping(benchmark_path: str):
    """Load benchmark, apply DSP mapping, analyze remaining cells."""
    print("="*70)
    print("ANALYZING POST-DSP CELLS")
    print("="*70)

    netlist = nextmap.NetlistDB('nextmap/schema.sql')
    with open(benchmark_path) as f:
        netlist.build_from_json(json.load(f)['modules']['top'])

    print(f"\n📂 Loaded: {benchmark_path}")

    # Count initial cells by type
    initial_stats = count_cells_by_type(netlist)
    print(f"\n📊 Initial cell counts:")
    for t, count in sorted(initial_stats.items()):
        print(f"  {t}: {count}")

    # Apply rewrites
    print("\n🔄 Applying rewrites...")
    netlist.rebuild()

    for iteration in range(10):
        comm_matches = nextmap.rewrites.ematch_comm(netlist, ["$adds", "$muls"])
        dff_fwd_matches = nextmap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$adds", "$muls"])
        dff_bwd_matches = nextmap.rewrites.ematch_dff_backward_aby_cell(netlist, ["$adds", "$muls"])

        cnt = 0
        cnt += nextmap.rewrites.apply_comm(netlist, comm_matches)
        cnt += nextmap.rewrites.apply_dff_forward_aby_cell(netlist, dff_fwd_matches)
        cnt += nextmap.rewrites.apply_dff_backward_aby_cell(netlist, dff_bwd_matches)

        if cnt > 0:
            print(f"  Iteration {iteration}: {cnt} rewrites")
            netlist.rebuild()
        else:
            print(f"  ✅ Converged after {iteration} iterations ({sum([cnt])} total)")
            break

    # SDFF rewrites
    print("\n🔄 Applying SDFF rewrites...")
    sdff_cnt = nextmap.rewrites.rewrite_sdff(netlist)
    print(f"  ✅ Applied {sdff_cnt} SDFF rewrites")

    # DSP tech mapping
    print("\n🎯 Applying DSP tech mapping...")
    dsp_rules = {
        "dsp_generic": {
            "requirements": {"dsp48e2": 1},
            "hidden_inputs": ["clk"],
            "inputs": ["inputs"],
            "outputs": ["outputs"]
        }
    }

    nextmap.rewrites.create_tech_tables(netlist, dsp_rules)
    try:
        nextmap.rewrites.rewrite_tech(netlist, dsp_rules)
        print("  ✅ DSP tech mapping completed")
    except Exception as e:
        print(f"  ⚠️  DSP tech mapping had issues: {e}")

    # Count cells after DSP mapping
    post_dsp_stats = count_cells_by_type(netlist)
    tech_stats = count_tech_cells(netlist)

    print(f"\n📊 Cells after DSP mapping:")
    for t, count in sorted(post_dsp_stats.items()):
        print(f"  {t}: {count}")

    print(f"\n🎯 Tech cells created:")
    for t, count in sorted(tech_stats.items()):
        print(f"  {t}: {count}")

    # Analyze what types could benefit from LUT mapping
    analyze_lut_opportunities(post_dsp_stats)

    return {
        'initial': initial_stats,
        'post_dsp': post_dsp_stats,
        'tech_cells': tech_stats
    }

def count_cells_by_type(netlist):
    """Count cells by type in aby_cells and ay_cells tables."""
    cell_counts = {}

    # Count aby_cells
    try:
        cur = netlist.execute("SELECT type, COUNT(*) FROM aby_cells GROUP BY type")
        for t, count in cur.fetchall():
            cell_counts[t] = count
    except:
        pass

    # Count ay_cells
    try:
        cur = netlist.execute("SELECT type, COUNT(*) FROM ay_cells GROUP BY type")
        for t, count in cur.fetchall():
            cell_counts[t] = cell_counts.get(t, 0) + count
    except:
        pass

    # Count dffs
    try:
        cur = netlist.execute("SELECT COUNT(*) FROM dffs")
        dff_count = cur.fetchone()[0]
        if dff_count > 0:
            cell_counts['$dff'] = dff_count
    except:
        pass

    return cell_counts

def count_tech_cells(netlist):
    """Count tech_* cells."""
    tech_counts = {}

    # Get all tech tables
    cur = netlist.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tech_%'")
    tech_tables = [row[0] for row in cur.fetchall()]

    for table in tech_tables:
        try:
            cur = netlist.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            if count > 0:
                tech_counts[table] = count
        except:
            pass

    return tech_counts

def analyze_lut_opportunities(cell_counts):
    """Analyze which cells could benefit from LUT mapping."""
    print(f"\n💡 LUT Mapping Opportunities:")

    # These operations could potentially be mapped to LUTs after bitblasting
    lut_candidates = {
        '$adds', '$addu', '$subs', '$subu',  # Arithmetic
        '$ands', '$andu', '$ors', '$oru', '$xors', '$xoru',  # Logic
        '$not', '$logic_not',  # Unary
        '$mux',  # Multiplexers
    }

    lut_mappable_count = sum(
        count for t, count in cell_counts.items()
        if any(cand in t for cand in lut_candidates)
    )

    print(f"  Cells that could benefit from LUT mapping: {lut_mappable_count}")

    if lut_mappable_count > 0:
        print(f"\n  Breakdown:")
        for t, count in sorted(cell_counts.items()):
            if any(cand in t for cand in lut_candidates):
                print(f"    {t}: {count}")

        print(f"\n  ✅ These cells could be bitblasted to AIG and LUT-mapped!")
        print(f"     Estimated gates after bitblasting: ~{lut_mappable_count * 8} (8-bit avg)")
    else:
        print(f"  ⚠️  No obvious LUT-mappable cells found")

def main():
    """Run analysis on systolic benchmarks."""
    print("🔬 POST-DSP CELL ANALYSIS FOR LUT MAPPING")
    print("="*70)

    benchmarks = [
        ('eval/out/systolic_matmul_4x4_w8.json', 'Systolic 4x4 w=8'),
    ]

    all_results = {}

    for path, name in benchmarks:
        print(f"\n\n{'#'*70}")
        print(f"# {name}")
        print(f"{'#'*70}")

        try:
            results = analyze_post_dsp_mapping(path)
            all_results[name] = results
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

    # Save results
    with open('post_dsp_analysis.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    print(f"\n\n💾 Results saved to: post_dsp_analysis.json")

if __name__ == "__main__":
    main()
