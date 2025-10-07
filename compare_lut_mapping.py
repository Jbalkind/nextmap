#!/usr/bin/env python3
"""
Compare nextmap performance with and without LUT mapping.

This script runs benchmarks in three modes:
1. Baseline: No LUT mapping
2. LUT Greedy: LUT mapping with greedy coverage algorithm (default)
3. LUT Random: LUT mapping with weighted random selection

Metrics compared:
- Total execution time
- Number of AND gates
- Number of inverters
- Number of LUTs created
- Wire coverage (for LUT modes)
"""

import sys
import os
import json
import time
from typing import Dict, Any

sys.path.insert(0, os.path.abspath('.'))

from emap.db import NetlistDB
import emap

# Benchmarks to test
# Note: Using adder (EPFL AIG benchmark) - this is a 128-bit adder
# FIR and systolic require word-level NetlistDB which has different schema
BENCHMARKS = [
    {
        'name': 'adder_25_luts',
        'json_path': 'eval/epfl/adder.json',
        'top_module': 'eval/epfl/adder',
        'max_iter': 4,
        'lut_count': 25
    },
    {
        'name': 'adder_50_luts',
        'json_path': 'eval/epfl/adder.json',
        'top_module': 'eval/epfl/adder',
        'max_iter': 4,
        'lut_count': 50
    },
    {
        'name': 'adder_100_luts',
        'json_path': 'eval/epfl/adder.json',
        'top_module': 'eval/epfl/adder',
        'max_iter': 4,
        'lut_count': 100
    },
    {
        'name': 'adder_200_luts',
        'json_path': 'eval/epfl/adder.json',
        'top_module': 'eval/epfl/adder',
        'max_iter': 4,
        'lut_count': 200
    },
]

def run_baseline(benchmark: Dict[str, Any]) -> Dict[str, Any]:
    """Run benchmark without LUT mapping."""
    print(f"\n{'='*60}")
    print(f"BASELINE: {benchmark['name']}")
    print(f"{'='*60}")

    start_time = time.time()

    # Create netlist
    netlist = NetlistDB(schema_file="emap/schema.sql", db_file=":memory:", cnt=10000)

    # Load benchmark
    with open(benchmark['json_path']) as f:
        data = json.load(f)
        netlist.build_from_json(data["modules"][benchmark['top_module']])

    # Apply rewrites
    wdsu = emap.DisjointSetUnion()
    netlist.rebuild(wdsu)

    total_rewrites = 0
    for i in range(benchmark['max_iter']):
        matches0 = emap.rewrites.ematch_not_idemp(netlist)
        matches1 = emap.rewrites.ematch_and_idemp(netlist)
        matches2 = emap.rewrites.ematch_and_assoc_left(netlist)
        matches3 = emap.rewrites.ematch_and_comm(netlist)
        matches4 = emap.rewrites.ematch_and_comp(netlist)

        cnt = 0
        cnt += emap.rewrites.apply_not_idemp(matches0, wdsu)
        cnt += emap.rewrites.apply_and_idemp(matches1, wdsu)
        cnt += emap.rewrites.apply_and_assoc_left(netlist, matches2)
        cnt += emap.rewrites.apply_and_comm(netlist, matches3)
        cnt += emap.rewrites.apply_and_comp(matches4, wdsu)

        total_rewrites += cnt
        if cnt > 0:
            netlist.rebuild(wdsu)
        else:
            break

    total_time = time.time() - start_time

    # Collect metrics
    and_cnt = netlist.and_cnt
    inv_cnt = netlist.inv_cnt

    print(f"  Time: {total_time:.3f}s")
    print(f"  Rewrites: {total_rewrites}")
    print(f"  AND gates: {and_cnt}")
    print(f"  Inverters: {inv_cnt}")

    return {
        'mode': 'baseline',
        'benchmark': benchmark['name'],
        'time': total_time,
        'rewrites': total_rewrites,
        'and_gates': and_cnt,
        'inverters': inv_cnt,
        'luts': 0,
        'coverage': 0
    }

def run_with_lut_mapping(benchmark: Dict[str, Any], greedy: bool = True) -> Dict[str, Any]:
    """Run benchmark with LUT mapping."""
    mode_name = "LUT GREEDY" if greedy else "LUT RANDOM"
    print(f"\n{'='*60}")
    print(f"{mode_name}: {benchmark['name']}")
    print(f"{'='*60}")

    start_time = time.time()

    # Create netlist
    netlist = NetlistDB(schema_file="emap/schema.sql", db_file=":memory:", cnt=10000)
    netlist.VERBOSE = False  # Reduce output

    # Load benchmark
    with open(benchmark['json_path']) as f:
        data = json.load(f)
        netlist.build_from_json(data["modules"][benchmark['top_module']])

    # Apply rewrites
    wdsu = emap.DisjointSetUnion()
    netlist.rebuild(wdsu)

    total_rewrites = 0
    for i in range(benchmark['max_iter']):
        matches0 = emap.rewrites.ematch_not_idemp(netlist)
        matches1 = emap.rewrites.ematch_and_idemp(netlist)
        matches2 = emap.rewrites.ematch_and_assoc_left(netlist)
        matches3 = emap.rewrites.ematch_and_comm(netlist)
        matches4 = emap.rewrites.ematch_and_comp(netlist)

        cnt = 0
        cnt += emap.rewrites.apply_not_idemp(matches0, wdsu)
        cnt += emap.rewrites.apply_and_idemp(matches1, wdsu)
        cnt += emap.rewrites.apply_and_assoc_left(netlist, matches2)
        cnt += emap.rewrites.apply_and_comm(netlist, matches3)
        cnt += emap.rewrites.apply_and_comp(matches4, wdsu)

        total_rewrites += cnt
        if cnt > 0:
            netlist.rebuild(wdsu)
        else:
            break

    # Apply LUT mapping
    lut_start = time.time()

    # Temporarily enable verbose to capture coverage
    old_verbose = netlist.VERBOSE
    netlist.VERBOSE = True

    import io
    import contextlib

    # Capture output to parse coverage
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        emap.rewrites.techmap_luts(
            netlist,
            k=6,
            cnt=benchmark['lut_count'],
            rseed=42,
            greedy=greedy
        )

    output = f.getvalue()
    netlist.VERBOSE = old_verbose

    lut_time = time.time() - lut_start
    total_time = time.time() - start_time

    # Parse output for coverage
    coverage = 0
    luts_created = 0
    for line in output.split('\n'):
        if 'Total wire coverage:' in line:
            coverage = int(line.split(':')[1].strip().split()[0])
        if 'Created' in line and 'LUTs' in line:
            luts_created = int(line.split()[1])

    # Collect metrics
    cur = netlist.execute("SELECT COUNT(*) FROM luts")
    lut_cnt = cur.fetchone()[0]
    and_cnt = netlist.and_cnt
    inv_cnt = netlist.inv_cnt

    print(f"  Time: {total_time:.3f}s (LUT mapping: {lut_time:.3f}s)")
    print(f"  Rewrites: {total_rewrites}")
    print(f"  AND gates: {and_cnt}")
    print(f"  Inverters: {inv_cnt}")
    print(f"  LUTs: {lut_cnt}")
    print(f"  Wire coverage: {coverage}")

    return {
        'mode': 'lut_greedy' if greedy else 'lut_random',
        'benchmark': benchmark['name'],
        'time': total_time,
        'lut_time': lut_time,
        'rewrites': total_rewrites,
        'and_gates': and_cnt,
        'inverters': inv_cnt,
        'luts': lut_cnt,
        'coverage': coverage
    }

def print_comparison(results: list):
    """Print comparison table."""
    print(f"\n{'='*80}")
    print("COMPARISON SUMMARY")
    print(f"{'='*80}")

    # Group by benchmark
    benchmarks = {}
    for result in results:
        bench = result['benchmark']
        if bench not in benchmarks:
            benchmarks[bench] = []
        benchmarks[bench].append(result)

    for bench_name, bench_results in benchmarks.items():
        print(f"\n{bench_name.upper()}")
        print(f"{'-'*80}")

        # Find baseline
        baseline = next((r for r in bench_results if r['mode'] == 'baseline'), None)

        for result in bench_results:
            mode = result['mode'].replace('_', ' ').title()
            print(f"{mode:15} | Time: {result['time']:6.3f}s | "
                  f"AND: {result['and_gates']:5} | INV: {result['inverters']:5} | "
                  f"LUTs: {result['luts']:3} | Coverage: {result['coverage']:4}")

            if baseline and result['mode'] != 'baseline':
                time_ratio = result['time'] / baseline['time']
                time_symbol = "🔴" if time_ratio > 1.1 else "🟢" if time_ratio < 0.9 else "🟡"
                print(f"                 {time_symbol} Time vs baseline: {time_ratio:.2f}x")

def main():
    """Run all comparisons."""
    print("🔍 COMPARING LUT MAPPING PERFORMANCE")
    print(f"Testing {len(BENCHMARKS)} benchmarks with 3 modes each")

    all_results = []

    for benchmark in BENCHMARKS:
        # Run baseline
        try:
            result = run_baseline(benchmark)
            all_results.append(result)
        except Exception as e:
            print(f"❌ Baseline failed: {e}")
            import traceback
            traceback.print_exc()

        # Run with greedy LUT mapping
        try:
            result = run_with_lut_mapping(benchmark, greedy=True)
            all_results.append(result)
        except Exception as e:
            print(f"❌ LUT greedy failed: {e}")
            import traceback
            traceback.print_exc()

        # Run with random LUT mapping
        try:
            result = run_with_lut_mapping(benchmark, greedy=False)
            all_results.append(result)
        except Exception as e:
            print(f"❌ LUT random failed: {e}")
            import traceback
            traceback.print_exc()

    # Print comparison
    print_comparison(all_results)

    # Save results
    with open('lut_comparison_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\n💾 Results saved to: lut_comparison_results.json")

if __name__ == "__main__":
    main()
