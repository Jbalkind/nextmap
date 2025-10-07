#!/usr/bin/env python3
"""
4-way test matrix: SQLite/PostgreSQL × Gurobi/CBC
"""

import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from test_dual_backend_systolic import run_systolic_test, check_postgresql_available, check_gurobi_available, check_cbc_available
import json

def run_4way_test():
    """Run the smallest test across all 4 backend+solver combinations."""

    # Test configuration
    test_name = "systolic_matmul_4x4_w8"
    dsp_limit = 16

    # Check available backends and solvers
    print("Checking availability...")
    postgres_available = check_postgresql_available()
    gurobi_available = check_gurobi_available()
    cbc_available = check_cbc_available()

    print(f"  PostgreSQL: {'✅' if postgres_available else '❌'}")
    print(f"  Gurobi: {'✅' if gurobi_available else '❌'}")
    print(f"  CBC: {'✅' if cbc_available else '❌'}")
    print()

    # Define test matrix
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

    # Run test matrix
    results = []
    total_configs = len(database_backends) * len(solver_backends)

    print(f"Running {total_configs} test configurations for {test_name}:")
    print("="*60)

    for db_backend in database_backends:
        for solver_backend in solver_backends:
            config_name = f"{db_backend}+{solver_backend}"
            print(f"Testing {config_name}...")

            try:
                result = run_systolic_test(test_name, dsp_limit, db_backend, solver_backend)
                results.append(result)

                if result['success']:
                    print(f"  ✅ SUCCESS - Time: {result['total_time']:.2f}s, Rewrites: {result['total_rewrites']}, DSPs: {result['dsps']}")
                else:
                    print(f"  ❌ FAILED - {result.get('error', 'Unknown error')}")

            except Exception as e:
                print(f"  ❌ EXCEPTION - {e}")
                results.append({
                    'test': test_name,
                    'database': db_backend,
                    'solver': solver_backend,
                    'config': config_name,
                    'success': False,
                    'error': str(e)
                })

            print()

    # Summary
    print("="*60)
    print("TEST SUMMARY:")
    print("="*60)

    successful_configs = [r for r in results if r['success']]
    failed_configs = [r for r in results if not r['success']]

    print(f"✅ Successful: {len(successful_configs)}/{len(results)} configurations")
    print(f"❌ Failed: {len(failed_configs)}/{len(results)} configurations")
    print()

    if successful_configs:
        print("SUCCESSFUL CONFIGURATIONS:")
        for result in successful_configs:
            print(f"  {result['config']}: {result['total_time']:.2f}s, {result['total_rewrites']} rewrites, {result['dsps']} DSPs")
        print()

    if failed_configs:
        print("FAILED CONFIGURATIONS:")
        for result in failed_configs:
            error_summary = result.get('error', 'Unknown error')
            if len(error_summary) > 100:
                error_summary = error_summary[:100] + "..."
            print(f"  {result['config']}: {error_summary}")
        print()

    # Performance comparison for successful configs
    if len(successful_configs) > 1:
        print("PERFORMANCE COMPARISON:")
        successful_configs.sort(key=lambda x: x['total_time'])
        fastest = successful_configs[0]
        print(f"  Fastest: {fastest['config']} ({fastest['total_time']:.2f}s)")

        for result in successful_configs[1:]:
            slowdown = result['total_time'] / fastest['total_time']
            print(f"  {result['config']}: {result['total_time']:.2f}s ({slowdown:.2f}x slower)")

    # Save detailed results
    with open('4way_test_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results saved to: 4way_test_results.json")

    return len(successful_configs) == len(results)

if __name__ == "__main__":
    success = run_4way_test()
    if success:
        print("\n🎉 ALL CONFIGURATIONS PASSED!")
        sys.exit(0)
    else:
        print("\n💥 SOME CONFIGURATIONS FAILED!")
        sys.exit(1)