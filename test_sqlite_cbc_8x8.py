#!/usr/bin/env python3
"""
Test CBC with SQLite backend on 8x8 case to isolate the issue
"""

import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from test_dual_backend_systolic import run_systolic_test

def test_sqlite_cbc_behavior():
    """Test CBC behavior on SQLite vs PostgreSQL for 8x8 case"""

    print("🔍 TESTING CBC BEHAVIOR: SQLite vs PostgreSQL")
    print("=" * 60)

    test_name = "systolic_matmul_8x8_w8"
    dsp_limit = 64

    print(f"Testing {test_name} with DSP limit {dsp_limit}")
    print()

    # Test SQLite + CBC
    print("1. SQLITE + CBC:")
    print("-" * 30)
    sqlite_result = run_systolic_test(test_name, dsp_limit, "sqlite", "cbc")

    print(f"Success: {sqlite_result['success']}")
    if sqlite_result['success']:
        print(f"Time: {sqlite_result['total_time']:.2f}s")
        print(f"DSPs: {sqlite_result['dsps']}")
        print(f"Cells: {sqlite_result['cells']}")
        print(f"Rewrites: {sqlite_result['total_rewrites']}")
    else:
        print(f"Error: {sqlite_result.get('error', 'Unknown error')}")

    print()

    # Test PostgreSQL + CBC
    print("2. POSTGRESQL + CBC:")
    print("-" * 30)
    postgres_result = run_systolic_test(test_name, dsp_limit, "postgresql", "cbc")

    print(f"Success: {postgres_result['success']}")
    if postgres_result['success']:
        print(f"Time: {postgres_result['total_time']:.2f}s")
        print(f"DSPs: {postgres_result['dsps']}")
        print(f"Cells: {postgres_result['cells']}")
        print(f"Rewrites: {postgres_result['total_rewrites']}")
    else:
        print(f"Error: {postgres_result.get('error', 'Unknown error')}")

    print()

    # Test both backends with Gurobi for comparison
    print("3. COMPARISON WITH GUROBI:")
    print("-" * 30)

    sqlite_gurobi = run_systolic_test(test_name, dsp_limit, "sqlite", "gurobi")
    postgres_gurobi = run_systolic_test(test_name, dsp_limit, "postgresql", "gurobi")

    print(f"SQLite + Gurobi: {sqlite_gurobi['success']}, DSPs: {sqlite_gurobi.get('dsps', 'N/A')}")
    print(f"PostgreSQL + Gurobi: {postgres_gurobi['success']}, DSPs: {postgres_gurobi.get('dsps', 'N/A')}")

    print()

    # Analysis
    print("📊 ANALYSIS:")
    print("=" * 60)

    if sqlite_result['success'] and not postgres_result['success']:
        print("🎯 CONCLUSION: PostgreSQL backend issue")
        print("   CBC works with SQLite but fails with PostgreSQL")
        print("   This suggests a database-specific constraint or transaction problem")

    elif not sqlite_result['success'] and not postgres_result['success']:
        print("🎯 CONCLUSION: CBC solver issue")
        print("   CBC fails on both backends for 8x8 case")
        print("   This suggests CBC has difficulty with the constraint structure")

    elif sqlite_result['success'] and postgres_result['success']:
        print("🎯 CONCLUSION: No CBC issue")
        print("   CBC works on both backends")
        print("   Previous failures may have been transient")

    else:  # SQLite fails, PostgreSQL succeeds (unlikely)
        print("🎯 CONCLUSION: SQLite backend issue")
        print("   Unexpected - PostgreSQL works but SQLite doesn't")

    # Check DSP usage patterns
    results = [
        ("SQLite + CBC", sqlite_result),
        ("PostgreSQL + CBC", postgres_result),
        ("SQLite + Gurobi", sqlite_gurobi),
        ("PostgreSQL + Gurobi", postgres_gurobi)
    ]

    print("\n🔍 DSP USAGE PATTERN:")
    for name, result in results:
        if result['success']:
            dsps = result.get('dsps', 'N/A')
            cells = result.get('cells', 'N/A')
            print(f"  {name:<20}: {dsps} DSPs, {cells} total cells")
        else:
            print(f"  {name:<20}: FAILED")

if __name__ == "__main__":
    test_sqlite_cbc_behavior()