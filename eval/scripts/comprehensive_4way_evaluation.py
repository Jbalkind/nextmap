#!/usr/bin/env python3
"""
Comprehensive 4-way Backend+Solver Evaluation
Tests multiple systolic array sizes across SQLite/PostgreSQL × Gurobi/CBC matrix
"""

import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from test_dual_backend_systolic import run_systolic_test, check_postgresql_available, check_gurobi_available, check_cbc_available
import json
import time
from datetime import datetime

def run_comprehensive_evaluation():
    """Run comprehensive evaluation across multiple test cases and all 4 backend+solver combinations."""

    # Check availability
    print("Checking availability...")
    postgres_available = check_postgresql_available()
    gurobi_available = check_gurobi_available()
    cbc_available = check_cbc_available()

    print(f"  PostgreSQL: {'✅' if postgres_available else '❌'}")
    print(f"  Gurobi: {'✅' if gurobi_available else '❌'}")
    print(f"  CBC: {'✅' if cbc_available else '❌'}")
    print()

    # Define test cases (in order of increasing complexity)
    # Skipping w32 cases as they can blow up in time
    test_cases = [
        ("systolic_matmul_4x4_w8", 16, "4x4 8-bit (fastest)"),
        ("systolic_matmul_4x4_w16", 16, "4x4 16-bit"),
        ("systolic_matmul_8x8_w8", 64, "8x8 8-bit (with SDFF rewrites)"),
        ("systolic_matmul_8x8_w16", 64, "8x8 16-bit (large)"),
    ]

    # Skip w32 cases to avoid time blowups
    large_tests = []

    for test_name, dsp_limit, desc in large_tests:
        if os.path.exists(f'eval/out/{test_name}.json'):
            test_cases.append((test_name, dsp_limit, desc))

    # Define backend matrix
    database_backends = ['sqlite']
    solver_backends = []

    if postgres_available:
        database_backends.append('postgresql')

    if gurobi_available:
        solver_backends.append('gurobi')

    if cbc_available:
        solver_backends.append('cbc')

    if not solver_backends:
        print("❌ No solvers available! Cannot run tests.")
        return

    print(f"Running {len(test_cases)} test cases × {len(database_backends)} databases × {len(solver_backends)} solvers = {len(test_cases) * len(database_backends) * len(solver_backends)} total configurations")
    print()

    # Run comprehensive evaluation
    all_results = []
    start_time = time.time()

    for i, (test_name, dsp_limit, description) in enumerate(test_cases):
        print(f"🧪 TEST {i+1}/{len(test_cases)}: {test_name} ({description})")
        print("="*80)

        # Check if test file exists
        test_file = f'eval/out/{test_name}.json'
        if not os.path.exists(test_file):
            print(f"⚠️ SKIPPING: {test_file} not found")
            print()
            continue

        test_results = []

        for db_backend in database_backends:
            for solver_backend in solver_backends:
                config_name = f"{db_backend}+{solver_backend}"
                print(f"  🔧 {config_name}...", end=" ", flush=True)

                try:
                    result = run_systolic_test(test_name, dsp_limit, db_backend, solver_backend)
                    test_results.append(result)
                    all_results.append(result)

                    if result['success']:
                        print(f"✅ {result['total_time']:.2f}s ({result['total_rewrites']} rewrites, {result['dsps']} DSPs)")
                    else:
                        error_msg = result.get('error', 'Unknown error')
                        if len(error_msg) > 60:
                            error_msg = error_msg[:60] + "..."
                        print(f"❌ FAILED: {error_msg}")

                except Exception as e:
                    print(f"❌ EXCEPTION: {e}")
                    error_result = {
                        'test': test_name,
                        'database': db_backend,
                        'solver': solver_backend,
                        'config': config_name,
                        'success': False,
                        'error': str(e)
                    }
                    test_results.append(error_result)
                    all_results.append(error_result)

        # Test case summary
        successful = [r for r in test_results if r['success']]
        if successful:
            fastest = min(successful, key=lambda x: x['total_time'])
            print(f"  🏆 Best: {fastest['config']} ({fastest['total_time']:.2f}s)")
        print()

    total_time = time.time() - start_time

    # Overall summary
    print("="*80)
    print("📊 COMPREHENSIVE EVALUATION SUMMARY")
    print("="*80)

    successful_results = [r for r in all_results if r['success']]
    failed_results = [r for r in all_results if not r['success']]

    print(f"✅ Successful: {len(successful_results)}/{len(all_results)} configurations")
    print(f"❌ Failed: {len(failed_results)}/{len(all_results)} configurations")
    print(f"⏱️ Total evaluation time: {total_time:.1f}s")
    print()

    # Performance analysis by test case
    if successful_results:
        print("📈 PERFORMANCE ANALYSIS BY TEST CASE:")
        print("-" * 80)

        for test_name, dsp_limit, description in test_cases:
            test_results = [r for r in successful_results if r['test'] == test_name]
            if not test_results:
                continue

            print(f"\n{test_name} ({description}):")

            # Sort by performance
            test_results.sort(key=lambda x: x['total_time'])
            fastest = test_results[0]

            for result in test_results:
                slowdown = result['total_time'] / fastest['total_time']
                print(f"  {result['config']:<20}: {result['total_time']:>6.2f}s ({slowdown:>4.1f}x) - {result['total_rewrites']} rewrites, {result['dsps']} DSPs")

        # Overall backend comparison
        print("\n📊 BACKEND PERFORMANCE COMPARISON:")
        print("-" * 80)

        backend_stats = {}
        for db in database_backends:
            for solver in solver_backends:
                config = f"{db}+{solver}"
                config_results = [r for r in successful_results if r['config'] == config]
                if config_results:
                    avg_time = sum(r['total_time'] for r in config_results) / len(config_results)
                    backend_stats[config] = {
                        'avg_time': avg_time,
                        'count': len(config_results),
                        'results': config_results
                    }

        if backend_stats:
            sorted_backends = sorted(backend_stats.items(), key=lambda x: x[1]['avg_time'])
            fastest_avg = sorted_backends[0][1]['avg_time']

            print(f"{'Configuration':<20} {'Avg Time':<10} {'Speedup':<8} {'Successful Tests':<15}")
            print("-" * 60)

            for config, stats in sorted_backends:
                speedup = fastest_avg / stats['avg_time']
                print(f"{config:<20} {stats['avg_time']:>8.2f}s {speedup:>6.1f}x {stats['count']:>13}")

    # Save detailed results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"comprehensive_evaluation_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            'timestamp': timestamp,
            'total_time': total_time,
            'test_cases': [{'name': name, 'dsp_limit': limit, 'description': desc} for name, limit, desc in test_cases],
            'database_backends': database_backends,
            'solver_backends': solver_backends,
            'results': all_results,
            'summary': {
                'total_configs': len(all_results),
                'successful': len(successful_results),
                'failed': len(failed_results),
                'success_rate': len(successful_results) / len(all_results) if all_results else 0
            }
        }, f, indent=2)

    print(f"\n💾 Detailed results saved to: {results_file}")

    return len(successful_results) == len(all_results)

if __name__ == "__main__":
    print("🚀 COMPREHENSIVE 4-WAY BACKEND+SOLVER EVALUATION")
    print("=" * 80)
    print()

    success = run_comprehensive_evaluation()

    if success:
        print("\n🎉 ALL CONFIGURATIONS PASSED!")
        sys.exit(0)
    else:
        print("\n⚠️ SOME CONFIGURATIONS FAILED!")
        sys.exit(1)