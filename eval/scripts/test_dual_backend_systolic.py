#!/usr/bin/env python3
"""
Comprehensive Backend & Solver Systolic Array Evaluation Test
Tests all 4 combinations: 2 databases (SQLite/PostgreSQL) × 2 solvers (Gurobi/CBC)
Compares results across the full test matrix on systolic matrix multiplication designs
"""

import nextmap
import json
import time
import os
import sys
import tempfile
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from itertools import product

def simple_cost_model(type_: str, *ports) -> float:
    """Standard cost model used in evaluations"""
    if type_ == "$dff":
        return len(ports[0]) * 1.0
    elif type_ in {"$muls", "$mulu"}:
        return len(ports[0]) * len(ports[1]) * 1.0
    elif type_ in {"$adds", "$addu", "$subs", "$subu"}:
        return min(len(ports[0]) + len(ports[1]), len(ports[2])) * 1.0
    return len(ports[0]) * 1.0

def check_postgresql_available() -> bool:
    """Check if PostgreSQL is available"""
    try:
        import psycopg2
        import getpass
        conn = psycopg2.connect(database='postgres', user=getpass.getuser())
        conn.close()
        return True
    except Exception:
        return False

def check_gurobi_available() -> bool:
    """Check if Gurobi is available"""
    try:
        from nextmap.extracts.solver_interface import create_solver
        solver = create_solver("gurobi")
        return True
    except Exception:
        return False

def check_cbc_available() -> bool:
    """Check if CBC is available"""
    try:
        from nextmap.extracts.solver_interface import create_solver
        solver = create_solver("cbc")
        return True
    except Exception:
        return False

def cleanup_postgresql_database() -> bool:
    """Clean up PostgreSQL database before running tests"""
    try:
        import psycopg2
        import getpass

        # Connect and drop/recreate the database
        conn = psycopg2.connect(database='postgres', user=getpass.getuser())
        conn.autocommit = True
        cur = conn.cursor()

        # Terminate any active connections to the target database
        cur.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = 'nextmap_temp' AND pid <> pg_backend_pid()
        """)

        # Drop and recreate the database
        cur.execute("DROP DATABASE IF EXISTS nextmap_temp")
        cur.execute("CREATE DATABASE nextmap_temp")

        cur.close()
        conn.close()
        return True

    except Exception as e:
        print(f"PostgreSQL database cleanup failed: {e}")
        return False

def run_systolic_test(test_name: str, dsp_limit: int, database_backend: str, solver_backend: str) -> Dict:
    """Run systolic test with specified database and solver backends"""
    config_name = f"{database_backend}+{solver_backend}"
    print(f"  {config_name}: Running {test_name}...")

    # DSP rules from eval_systolic_v2.ipynb
    dsp_rules = {
        'dsp_generic': {
            'requirements': {
                'dsp48e2': 1
            },
            'hidden_inputs': ['clk'],
            'inputs': ['inputs'],
            'outputs': ['outputs']
        }
    }

    db_file = None
    try:
        start_time = time.time()

        # Phase 1: Database initialization
        init_start = time.time()
        if database_backend == "sqlite":
            # Use temporary SQLite file
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_db:
                db_file = tmp_db.name
            netlist = nextmap.NetlistDB("nextmap/schema.sql", db_file, backend='sqlite')
        else:  # postgresql
            # Clean PostgreSQL database first
            if not cleanup_postgresql_database():
                return {
                    'test': test_name, 'database': database_backend, 'solver': solver_backend,
                    'success': False, 'error': 'Database cleanup failed'
                }
            # Use the factory function without specifying schema - it will auto-select PostgreSQL schema
            netlist = nextmap.NetlistDB("nextmap/schema.sql", None, backend='postgres')

        # Load and build JSON
        with open(f'eval/out/{test_name}.json', 'r') as f:
            json_data = json.load(f)
        netlist.build_from_json(json_data['modules']['top'])
        netlist.rebuild()
        init_time = time.time() - init_start

        # Phase 2: Apply rewrites (from eval_systolic_v2.ipynb)
        rewrite_start = time.time()
        total_rewrites = 0
        iterations = 0
        cnt = 1

        # Limit iterations for faster testing
        max_iterations = 5

        while cnt > 0 and iterations < max_iterations:
            iterations += 1
            cnt = 0

            # Apply rewrites
            comm_matches = nextmap.rewrites.ematch_comm(netlist, ["$adds", "$muls"])
            cnt += nextmap.rewrites.apply_comm(netlist, comm_matches)

            dff_forward_aby_cell_matches = nextmap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$adds", "$muls"])
            cnt += nextmap.rewrites.apply_dff_forward_aby_cell(netlist, dff_forward_aby_cell_matches)

            dff_backward_aby_cell_matches = nextmap.rewrites.ematch_dff_backward_aby_cell(netlist, ["$adds", "$muls"])
            cnt += nextmap.rewrites.apply_dff_backward_aby_cell(netlist, dff_backward_aby_cell_matches)

            total_rewrites += cnt
            if cnt > 0:
                netlist.rebuild()

        # Phase 3: Handle 8x8 designs with SDFF rewrites
        sdff_start = time.time()
        sdff_rewrites = 0
        if "8x8" in test_name:
            sdff_rewrites = nextmap.rewrites.rewrite_sdff(netlist)
        sdff_time = time.time() - sdff_start

        # Phase 4: Handle w32 designs with wide splits
        wide_split_start = time.time()
        wide_split_rewrites = 0
        if "w32" in test_name:
            matches = nextmap.rewrites.ematch_wide_muls(netlist)
            cnt = nextmap.rewrites.apply_wide_muls_split(netlist, matches)
            wide_split_rewrites += cnt
            if cnt > 0:
                netlist.rebuild()

            matches = nextmap.rewrites.ematch_wide_dff(netlist)
            cnt = nextmap.rewrites.apply_wide_dff_split(netlist, matches)
            wide_split_rewrites += cnt
            if cnt > 0:
                netlist.rebuild()
        wide_split_time = time.time() - wide_split_start

        rewrite_time = time.time() - rewrite_start

        # Phase 5: Tech mapping
        techmap_start = time.time()
        nextmap.rewrites.create_tech_tables(netlist, dsp_rules)
        nextmap.rewrites.techmap_dsp(netlist)
        techmap_time = time.time() - techmap_start

        # Phase 6: ILP extraction with specified solver
        extraction_start = time.time()
        mod = nextmap.extracts.ilp.extract_techmap_with_limit(
            netlist,
            simple_cost_model,
            dsp_rules,
            {"dsp48e2": dsp_limit},
            solver_type=solver_backend.lower()
        )
        extraction_time = time.time() - extraction_start

        # Results
        total_time = time.time() - start_time
        num_cells = len(mod.get('cells', {}))
        dsp_cells = sum(1 for cell in mod.get('cells', {}).values() if cell.get('type') == 'dsp_generic')

        # Cleanup SQLite file
        if db_file and os.path.exists(db_file):
            try:
                os.unlink(db_file)
            except:
                pass

        return {
            'test': test_name,
            'database': database_backend,
            'solver': solver_backend,
            'config': config_name,
            'success': True,
            'total_time': total_time,
            'init_time': init_time,
            'rewrite_time': rewrite_time,
            'sdff_time': sdff_time,
            'wide_split_time': wide_split_time,
            'techmap_time': techmap_time,
            'extraction_time': extraction_time,
            'total_rewrites': total_rewrites,
            'sdff_rewrites': sdff_rewrites,
            'wide_split_rewrites': wide_split_rewrites,
            'iterations': iterations,
            'cells': num_cells,
            'dsps': dsp_cells,
            'dsp_limit': dsp_limit
        }

    except Exception as e:
        # Cleanup on error
        if db_file and os.path.exists(db_file):
            try:
                os.unlink(db_file)
            except:
                pass

        return {
            'test': test_name,
            'database': database_backend,
            'solver': solver_backend,
            'config': config_name,
            'success': False,
            'error': str(e)
        }

def generate_designs():
    """Generate systolic designs using yosys"""
    print("Checking for systolic designs...")

    os.makedirs("eval/out", exist_ok=True)

    designs = [
        ("eval/systolic/systolic_matmul_4x4_w8.v", "eval/out/systolic_matmul_4x4_w8.json"),
        ("eval/systolic/systolic_matmul_4x4_w16.v", "eval/out/systolic_matmul_4x4_w16.json"),
        ("eval/systolic/systolic_matmul_4x4_w32.v", "eval/out/systolic_matmul_4x4_w32.json"),
        ("eval/systolic/systolic_matmul_8x8_w8.v", "eval/out/systolic_matmul_8x8_w8.json"),
        ("eval/systolic/systolic_matmul_8x8_w16.v", "eval/out/systolic_matmul_8x8_w16.json"),
    ]

    need_generation = False
    for verilog_file, json_file in designs:
        if os.path.exists(verilog_file) and not os.path.exists(json_file):
            need_generation = True
            break

    if need_generation:
        print("Generating missing designs with yosys...")
        # Requires `yosys` on PATH.
        for verilog_file, json_file in designs:
            if os.path.exists(verilog_file) and not os.path.exists(json_file):
                cmd = f'yosys -q -p "read_verilog {verilog_file}; proc; memory_map; opt; write_json {json_file}"'
                print(f"  Generating {json_file}...")
                os.system(cmd)

def print_test_matrix_table(results: List[Dict]):
    """Print a comprehensive test matrix table"""
    print(f"\n{'='*120}")
    print("COMPREHENSIVE TEST MATRIX RESULTS")
    print(f"{'='*120}")

    # Group results by test case
    test_cases = {}
    for result in results:
        if result['success']:
            test_name = result['test']
            if test_name not in test_cases:
                test_cases[test_name] = {}
            config = f"{result['database']}+{result['solver']}"
            test_cases[test_name][config] = result

    # Print header
    print(f"{'Test Case':<25} {'Config':<15} {'Total(s)':<8} {'Init(s)':<7} {'Rewrite(s)':<9} {'Tech(s)':<7} {'Extract(s)':<9} {'Rewrites':<8} {'Cells':<6} {'DSPs':<5}")
    print("-" * 120)

    # Print results for each test case
    for test_name in sorted(test_cases.keys()):
        configs = test_cases[test_name]

        # Print each configuration
        for config in ['sqlite+gurobi', 'sqlite+cbc', 'postgresql+gurobi', 'postgresql+cbc']:
            if config in configs:
                r = configs[config]
                print(f"{test_name:<25} {config:<15} {r['total_time']:<8.3f} {r['init_time']:<7.3f} {r['rewrite_time']:<9.3f} {r['techmap_time']:<7.3f} {r['extraction_time']:<9.3f} {r['total_rewrites']:<8} {r['cells']:<6} {r['dsps']:<5}")

        print()  # Add space between test cases

def print_performance_analysis(results: List[Dict]):
    """Print detailed performance analysis across all configurations"""
    successful_results = [r for r in results if r['success']]

    if not successful_results:
        print("No successful results to analyze")
        return

    print(f"\n{'='*80}")
    print("PERFORMANCE ANALYSIS")
    print(f"{'='*80}")

    # Group by database backend
    sqlite_results = [r for r in successful_results if r['database'] == 'sqlite']
    postgres_results = [r for r in successful_results if r['database'] == 'postgresql']

    # Group by solver backend
    gurobi_results = [r for r in successful_results if r['solver'] == 'gurobi']
    cbc_results = [r for r in successful_results if r['solver'] == 'cbc']

    # Database comparison
    if sqlite_results and postgres_results:
        print(f"\n📊 DATABASE BACKEND COMPARISON:")
        sqlite_avg_total = statistics.mean([r['total_time'] for r in sqlite_results])
        postgres_avg_total = statistics.mean([r['total_time'] for r in postgres_results])

        sqlite_avg_extract = statistics.mean([r['extraction_time'] for r in sqlite_results])
        postgres_avg_extract = statistics.mean([r['extraction_time'] for r in postgres_results])

        print(f"   Average Total Time:")
        print(f"     SQLite:     {sqlite_avg_total:.3f}s")
        print(f"     PostgreSQL: {postgres_avg_total:.3f}s")
        faster_db = 'PostgreSQL' if postgres_avg_total < sqlite_avg_total else 'SQLite'
        speedup_db = abs(sqlite_avg_total/postgres_avg_total - 1)*100
        print(f"     Winner:     {faster_db} ({speedup_db:.1f}% faster)")

        print(f"   Average Extraction Time:")
        print(f"     SQLite:     {sqlite_avg_extract:.3f}s")
        print(f"     PostgreSQL: {postgres_avg_extract:.3f}s")
        faster_db_extract = 'PostgreSQL' if postgres_avg_extract < sqlite_avg_extract else 'SQLite'
        speedup_db_extract = abs(sqlite_avg_extract/postgres_avg_extract - 1)*100
        print(f"     Winner:     {faster_db_extract} ({speedup_db_extract:.1f}% faster)")

    # Solver comparison
    if gurobi_results and cbc_results:
        print(f"\n🔧 SOLVER BACKEND COMPARISON:")
        gurobi_avg_total = statistics.mean([r['total_time'] for r in gurobi_results])
        cbc_avg_total = statistics.mean([r['total_time'] for r in cbc_results])

        gurobi_avg_extract = statistics.mean([r['extraction_time'] for r in gurobi_results])
        cbc_avg_extract = statistics.mean([r['extraction_time'] for r in cbc_results])

        print(f"   Average Total Time:")
        print(f"     Gurobi: {gurobi_avg_total:.3f}s")
        print(f"     CBC:    {cbc_avg_total:.3f}s")
        faster_solver = 'CBC' if cbc_avg_total < gurobi_avg_total else 'Gurobi'
        speedup_solver = abs(gurobi_avg_total/cbc_avg_total - 1)*100
        print(f"     Winner: {faster_solver} ({speedup_solver:.1f}% faster)")

        print(f"   Average Extraction Time:")
        print(f"     Gurobi: {gurobi_avg_extract:.3f}s")
        print(f"     CBC:    {cbc_avg_extract:.3f}s")
        faster_solver_extract = 'CBC' if cbc_avg_extract < gurobi_avg_extract else 'Gurobi'
        speedup_solver_extract = abs(gurobi_avg_extract/cbc_avg_extract - 1)*100
        print(f"     Winner: {faster_solver_extract} ({speedup_solver_extract:.1f}% faster)")

    # Configuration ranking
    print(f"\n🏆 CONFIGURATION RANKING (by average total time):")
    config_performance = {}
    for config in ['sqlite+gurobi', 'sqlite+cbc', 'postgresql+gurobi', 'postgresql+cbc']:
        config_results = [r for r in successful_results if f"{r['database']}+{r['solver']}" == config]
        if config_results:
            avg_time = statistics.mean([r['total_time'] for r in config_results])
            config_performance[config] = avg_time

    for i, (config, avg_time) in enumerate(sorted(config_performance.items(), key=lambda x: x[1]), 1):
        print(f"   {i}. {config:<18} {avg_time:.3f}s average")

def print_correctness_analysis(results: List[Dict]):
    """Print correctness verification across all configurations"""
    successful_results = [r for r in results if r['success']]

    print(f"\n✅ CORRECTNESS VERIFICATION:")
    correctness_issues = []

    # Group by test case
    test_groups = {}
    for result in successful_results:
        test_name = result['test']
        if test_name not in test_groups:
            test_groups[test_name] = {}
        config = f"{result['database']}+{result['solver']}"
        test_groups[test_name][config] = result

    for test_name, configs in test_groups.items():
        # Check if all configurations give the same DSP count
        dsp_counts = set(config_result['dsps'] for config_result in configs.values())

        if len(dsp_counts) == 1:
            dsp_count = list(dsp_counts)[0]
            print(f"   ✓ {test_name}: All {len(configs)} configurations extracted {dsp_count} DSPs")
        else:
            print(f"   ⚠ {test_name}: Inconsistent results across configurations:")
            for config, result in sorted(configs.items()):
                print(f"      {config}: {result['dsps']} DSPs")
            correctness_issues.append(test_name)

    if not correctness_issues:
        print(f"   🎉 All test cases show consistent DSP extraction across all configurations!")
    else:
        print(f"   ⚠  {len(correctness_issues)} test case(s) show different results between configurations")

def main():
    """Run comprehensive backend and solver evaluation tests"""
    print("="*80)
    print("COMPREHENSIVE BACKEND & SOLVER SYSTOLIC ARRAY EVALUATION TEST")
    print("="*80)
    print("Testing all 4 combinations: 2 databases × 2 solvers")
    print("Databases: SQLite, PostgreSQL")
    print("Solvers:   Gurobi, CBC")
    print()

    # Check availability
    postgres_available = check_postgresql_available()
    gurobi_available = check_gurobi_available()
    cbc_available = check_cbc_available()

    print("Backend Availability:")
    print(f"  PostgreSQL: {'✓' if postgres_available else '✗'}")
    print(f"  Gurobi:     {'✓' if gurobi_available else '✗'}")
    print(f"  CBC:        {'✓' if cbc_available else '✗'}")
    print()

    # Determine test matrix
    databases = ['sqlite']
    if postgres_available:
        databases.append('postgresql')

    solvers = []
    if gurobi_available:
        solvers.append('gurobi')
    if cbc_available:
        solvers.append('cbc')

    if not solvers:
        print("❌ No solvers available! Please install Gurobi or CBC.")
        return

    print(f"Testing {len(databases)} databases × {len(solvers)} solvers = {len(databases) * len(solvers)} configurations")
    print()

    # Generate designs
    generate_designs()

    # Test cases - only the smallest two for quick verification
    test_cases = [
        ("systolic_matmul_4x4_w8", 16),   # Smallest and fastest test case
        ("systolic_matmul_4x4_w16", 16),  # 4x4 with 16-bit width
        # ("systolic_matmul_4x4_w32", 16),  # 4x4 with 32-bit width (includes wide splits)
        # ("systolic_matmul_8x8_w8", 64),   # 8x8 with 8-bit width (includes SDFF rewrites)
        # ("systolic_matmul_8x8_w16", 64),  # 8x8 with 16-bit width (largest test case)
    ]

    results = []

    for test_name, dsp_limit in test_cases:
        json_path = f'eval/out/{test_name}.json'

        if not os.path.exists(json_path):
            print(f"\nSkipping {test_name}: {json_path} not found")
            continue

        print(f"\n=== {test_name} (DSP limit: {dsp_limit}) ===")

        # Test all combinations
        for database, solver in product(databases, solvers):
            result = run_systolic_test(test_name, dsp_limit, database, solver)
            results.append(result)

            config_name = f"{database}+{solver}"
            if result['success']:
                print(f"    ✓ {config_name}: {result['total_time']:.3f}s total, {result['dsps']} DSPs extracted")
            else:
                print(f"    ✗ {config_name}: FAILED - {result.get('error', 'Unknown error')}")

    # Print comprehensive results
    print_test_matrix_table(results)
    print_performance_analysis(results)
    print_correctness_analysis(results)

    # Final summary
    successful_tests = sum(1 for r in results if r['success'])
    total_tests = len(results)

    print(f"\n{'='*80}")
    print("FINAL SUMMARY")
    print(f"{'='*80}")
    print(f"Overall: {successful_tests}/{total_tests} test configurations passed")

    if successful_tests == total_tests:
        print("🎉 All test configurations PASSED!")
    else:
        print("⚠️  Some test configurations failed. Check output above.")

    # Save results to JSON for further analysis
    with open("comprehensive_backend_solver_results.json", 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results saved to comprehensive_backend_solver_results.json")

if __name__ == "__main__":
    main()