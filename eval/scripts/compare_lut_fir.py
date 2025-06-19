#!/usr/bin/env python3
"""
Compare LUT mapping on FIR benchmark using equality saturation to get AIG.

This script:
1. Loads FIR design (word-level)
2. Applies equality saturation rewrites (keeps word-level structure)
3. Extracts AND/INV netlist from e-graph
4. Applies LUT mapping to the extracted AIG
5. Compares baseline vs LUT mapping
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.abspath('.'))

from nextmap.db_aig import NetlistDB
from nextmap.rewrites import aig_opt
import nextmap

def load_and_saturate_fir(benchmark_path: str, verbose: bool = False):
    """Load FIR benchmark and apply equality saturation."""

    print(f"Loading {benchmark_path}...")
    netlist = NetlistDB(schema_file="nextmap/schema.sql", db_file=":memory:", cnt=10000)
    netlist.VERBOSE = verbose

    with open(benchmark_path) as f:
        data = json.load(f)
        netlist.build_from_json(data["modules"]["top"])

    print(f"Initial gates: AND={netlist.and_cnt}, INV={netlist.inv_cnt}")

    # Apply equality saturation rewrites
    print("Applying equality saturation...")
    wdsu = nextmap.DisjointSetUnion()
    netlist.rebuild(wdsu)

    total_rewrites = 0
    for iteration in range(10):  # More iterations for convergence
        cnt = aig_opt.optimize_aig_with_eqsat(netlist, max_iterations=1)['total_rewrites']

        total_rewrites += cnt
        if cnt > 0:
            if verbose:
                print(f"  Iteration {iteration}: {cnt} rewrites")
            netlist.rebuild(wdsu)
        else:
            print(f"  Converged after {iteration} iterations")
            break

    print(f"Total rewrites: {total_rewrites}")
    print(f"Optimized gates: AND={netlist.and_cnt}, INV={netlist.inv_cnt}")

    return netlist

def run_baseline(benchmark_path: str):
    """Run without LUT mapping."""
    print(f"\n{'='*70}")
    print("BASELINE (No LUT Mapping)")
    print(f"{'='*70}")

    start_time = time.time()
    netlist = load_and_saturate_fir(benchmark_path, verbose=False)
    elapsed = time.time() - start_time

    and_cnt = netlist.and_cnt
    inv_cnt = netlist.inv_cnt
    total = and_cnt + inv_cnt

    print(f"\n✅ Results:")
    print(f"  Time: {elapsed:.3f}s")
    print(f"  AND gates: {and_cnt}")
    print(f"  Inverters: {inv_cnt}")
    print(f"  Total gates: {total}")

    return {
        'mode': 'baseline',
        'time': elapsed,
        'and_gates': and_cnt,
        'inverters': inv_cnt,
        'total_gates': total,
        'luts': 0,
        'coverage': 0
    }

def run_with_lut_mapping(benchmark_path: str, lut_count: int, greedy: bool = True):
    """Run with LUT mapping."""
    mode_name = "GREEDY LUT MAPPING" if greedy else "RANDOM LUT MAPPING"
    print(f"\n{'='*70}")
    print(f"{mode_name} ({lut_count} LUTs)")
    print(f"{'='*70}")

    start_time = time.time()
    netlist = load_and_saturate_fir(benchmark_path, verbose=False)

    # Apply LUT mapping to the extracted AIG
    print(f"\nApplying LUT mapping (greedy={greedy})...")
    lut_start = time.time()

    # Capture output to parse coverage
    import io
    import contextlib

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        old_verbose = netlist.VERBOSE
        netlist.VERBOSE = True
        nextmap.rewrites.techmap_luts(netlist, k=6, cnt=lut_count, rseed=42, greedy=greedy)
        netlist.VERBOSE = old_verbose

    output = f.getvalue()
    lut_time = time.time() - lut_start
    elapsed = time.time() - start_time

    # Parse coverage from output
    coverage = 0
    luts_created = 0
    for line in output.split('\n'):
        if 'Total wire coverage:' in line:
            coverage = int(line.split(':')[1].strip().split()[0])
        if 'Created' in line and 'LUTs' in line:
            luts_created = int(line.split()[1])

    and_cnt = netlist.and_cnt
    inv_cnt = netlist.inv_cnt
    total = and_cnt + inv_cnt

    # Get actual LUT count from DB
    cur = netlist.execute("SELECT COUNT(*) FROM luts")
    actual_lut_cnt = cur.fetchone()[0]

    print(f"\n✅ Results:")
    print(f"  Time: {elapsed:.3f}s (LUT mapping: {lut_time:.3f}s)")
    print(f"  AND gates: {and_cnt}")
    print(f"  Inverters: {inv_cnt}")
    print(f"  Total gates: {total}")
    print(f"  LUTs created: {actual_lut_cnt}")
    print(f"  Wire coverage: {coverage}")

    return {
        'mode': 'lut_greedy' if greedy else 'lut_random',
        'time': elapsed,
        'lut_time': lut_time,
        'and_gates': and_cnt,
        'inverters': inv_cnt,
        'total_gates': total,
        'luts': actual_lut_cnt,
        'coverage': coverage
    }

def main():
    """Run comparison on FIR benchmark."""

    # Test different FIR sizes
    # The AIG LUT backend is combinational, so these are the combinational
    # ($and/$not) AIGs produced by eval/scripts/synth_designs.sh (registers cut).
    benchmarks = [
        ('eval/out/fir_n16_w8_aig.json', 50, 'FIR n=16 w=8'),
        ('eval/out/fir_n16_w8_aig.json', 100, 'FIR n=16 w=8 (100 LUTs)'),
        ('eval/out/fir_n16_w16_aig.json', 100, 'FIR n=16 w=16 (100 LUTs)'),
    ]

    all_results = []

    for benchmark_path, lut_count, name in benchmarks:
        print(f"\n{'#'*70}")
        print(f"# {name}")
        print(f"{'#'*70}")

        # Run baseline
        try:
            result = run_baseline(benchmark_path)
            result['benchmark'] = name
            all_results.append(result)
        except Exception as e:
            print(f"❌ Baseline failed: {e}")
            import traceback
            traceback.print_exc()
            continue

        # Run with greedy LUT mapping
        try:
            result = run_with_lut_mapping(benchmark_path, lut_count, greedy=True)
            result['benchmark'] = name
            all_results.append(result)
        except Exception as e:
            print(f"❌ Greedy LUT mapping failed: {e}")
            import traceback
            traceback.print_exc()

        # Run with random LUT mapping
        try:
            result = run_with_lut_mapping(benchmark_path, lut_count, greedy=False)
            result['benchmark'] = name
            all_results.append(result)
        except Exception as e:
            print(f"❌ Random LUT mapping failed: {e}")
            import traceback
            traceback.print_exc()

    # Print comparison summary
    print(f"\n{'='*80}")
    print("COMPARISON SUMMARY")
    print(f"{'='*80}")

    # Group by benchmark
    benchmarks_grouped = {}
    for result in all_results:
        bench = result['benchmark']
        if bench not in benchmarks_grouped:
            benchmarks_grouped[bench] = []
        benchmarks_grouped[bench].append(result)

    for bench_name, results in benchmarks_grouped.items():
        print(f"\n{bench_name}")
        print(f"{'-'*80}")

        baseline = next((r for r in results if r['mode'] == 'baseline'), None)

        for result in results:
            mode = result['mode'].replace('_', ' ').title()
            print(f"{mode:15} | Time: {result['time']:6.3f}s | "
                  f"Gates: {result['total_gates']:6} | "
                  f"LUTs: {result['luts']:4} | Coverage: {result['coverage']:5}")

            if baseline and result['mode'] != 'baseline':
                time_ratio = result['time'] / baseline['time']
                time_symbol = "🔴" if time_ratio > 1.2 else "🟢" if time_ratio < 0.9 else "🟡"

                gate_reduction = baseline['total_gates'] - result['coverage']
                effective_gates = result['total_gates'] + result['luts'] - result['coverage']

                print(f"                 {time_symbol} Time vs baseline: {time_ratio:.2f}x")
                print(f"                 📊 Coverage reduces ~{result['coverage']} gates to {result['luts']} LUTs")
                print(f"                 📊 Effective complexity: {effective_gates} (gates-coverage+LUTs)")

    # Save results
    with open('fir_lut_comparison.json', 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\n💾 Results saved to: fir_lut_comparison.json")

if __name__ == "__main__":
    main()
