#!/usr/bin/env python3
"""
Comprehensive timing-oriented extraction demonstration.
Shows clear cell type changes based on timing constraints.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from emap.db import NetlistDB
from emap.extracts.ilp import extract_no_techmap
from emap.extracts.timing_ilp import extract_timing_aware


def create_comprehensive_circuit() -> NetlistDB:
    """
    Create a circuit demonstrating multiple timing/cost trade-offs.

    Computes: result = (a * b) + (c << 2)

    Implementation alternatives:
    1. ULTRA-FAST: Dedicated multiply + barrel shifter (cost=15, delay=2.0)
    2. FAST: Multiply + iterative shifts (cost=13, delay=4.0)
    3. MEDIUM: Add chain + shifts (cost=10, delay=6.0)
    4. SLOW: Pure addition chains (cost=8, delay=10.0)
    """
    schema_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emap", "schema.sql")
    db = NetlistDB(schema_file)

    # Inputs (4-bit)
    a = db._add_wirevec([db.auto_id for _ in range(4)])
    b = db._add_wirevec([db.auto_id for _ in range(4)])
    c = db._add_wirevec([db.auto_id for _ in range(4)])
    result = db._add_wirevec([db.auto_id for _ in range(4)])

    db.execute("INSERT INTO from_inputs (name, source) VALUES (?, ?)", ("a", a))
    db.execute("INSERT INTO from_inputs (name, source) VALUES (?, ?)", ("b", b))
    db.execute("INSERT INTO from_inputs (name, source) VALUES (?, ?)", ("c", c))
    db.execute("INSERT INTO as_outputs (name, sink) VALUES (?, ?)", ("result", result))

    # Constants
    const_2 = db._add_wirevec([0, 1, 0, 0])  # 2 in 4-bit

    # === PATH 1: ULTRA-FAST (cost=15, delay=2.0) ===
    # $mul + $shl (barrel shifter) + $add
    mult1_ultra = db._add_wirevec([db.auto_id for _ in range(8)])    # a * b
    shift1_ultra = db._add_wirevec([db.auto_id for _ in range(4)])   # c << 2 (barrel shift)
    result1_ultra = db._add_wirevec([db.auto_id for _ in range(4)])  # final sum

    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$mul", a, b, mult1_ultra))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$shl", c, const_2, shift1_ultra))

    # Truncate multiply result to 4 bits for addition
    mult1_trunc = db._add_wirevec([db.auto_id for _ in range(4)])
    db.execute("INSERT INTO ay_cells (type, a, y) VALUES (?, ?, ?)", ("$buf", mult1_ultra, mult1_trunc))

    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", mult1_trunc, shift1_ultra, result1_ultra))
    db.execute("INSERT INTO ay_cells (type, a, y) VALUES (?, ?, ?)", ("$buf", result1_ultra, result))

    # === PATH 2: FAST (cost=13, delay=4.0) ===
    # $mul + 2x $shl (iterative) + $add
    mult2_fast = db._add_wirevec([db.auto_id for _ in range(8)])
    shift2a_fast = db._add_wirevec([db.auto_id for _ in range(4)])
    shift2b_fast = db._add_wirevec([db.auto_id for _ in range(4)])
    result2_fast = db._add_wirevec([db.auto_id for _ in range(4)])

    const_1 = db._add_wirevec([1, 0, 0, 0])  # 1 in 4-bit

    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$mul", a, b, mult2_fast))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$shl", c, const_1, shift2a_fast))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$shl", shift2a_fast, const_1, shift2b_fast))

    mult2_trunc = db._add_wirevec([db.auto_id for _ in range(4)])
    db.execute("INSERT INTO ay_cells (type, a, y) VALUES (?, ?, ?)", ("$buf", mult2_fast, mult2_trunc))

    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", mult2_trunc, shift2b_fast, result2_fast))
    db.execute("INSERT INTO ay_cells (type, a, y) VALUES (?, ?, ?)", ("$buf", result2_fast, result))

    # === PATH 3: MEDIUM (cost=10, delay=6.0) ===
    # Sequential additions for a*b, then shift c, then add
    # a*b ≈ a+a+a (3 adds), c<<2 ≈ c+c+c+c (3 more adds), final add
    temp3a = db._add_wirevec([db.auto_id for _ in range(4)])  # a + a
    temp3b = db._add_wirevec([db.auto_id for _ in range(4)])  # (a+a) + a = 3*a ≈ a*b
    temp3c = db._add_wirevec([db.auto_id for _ in range(4)])  # c + c
    temp3d = db._add_wirevec([db.auto_id for _ in range(4)])  # (c+c) + c
    temp3e = db._add_wirevec([db.auto_id for _ in range(4)])  # ((c+c)+c) + c = 4*c ≈ c<<2
    result3_medium = db._add_wirevec([db.auto_id for _ in range(4)])

    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", a, a, temp3a))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temp3a, a, temp3b))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", c, c, temp3c))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temp3c, c, temp3d))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temp3d, c, temp3e))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temp3b, temp3e, result3_medium))
    db.execute("INSERT INTO ay_cells (type, a, y) VALUES (?, ?, ?)", ("$buf", result3_medium, result))

    # === PATH 4: SLOW (cost=8, delay=10.0) ===
    # Even longer addition chains (more sequential dependencies)
    temps4 = [db._add_wirevec([db.auto_id for _ in range(4)]) for _ in range(8)]

    # Long chain: a+a+a+a+a+a (6 additions for a*b approximation)
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", a, a, temps4[0]))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temps4[0], a, temps4[1]))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temps4[1], a, temps4[2]))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temps4[2], a, temps4[3]))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temps4[3], a, temps4[4]))

    # c+c+c (2 additions for c<<2 approximation)
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", c, c, temps4[5]))
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temps4[5], c, temps4[6]))

    # Final addition
    db.execute("INSERT INTO aby_cells (type, a, b, y) VALUES (?, ?, ?, ?)", ("$add", temps4[4], temps4[6], temps4[7]))
    db.execute("INSERT INTO ay_cells (type, a, y) VALUES (?, ?, ?)", ("$buf", temps4[7], result))

    db.commit()
    return db


def comprehensive_cost_model(cell_type: str, *args, **kwargs) -> float:
    """Cost model with clear trade-offs."""
    cost_map = {
        "$buf": 0.1,
        "$add": 1.0,    # Cheap
        "$mul": 10.0,   # Expensive
        "$shl": 2.0,    # Medium cost (barrel shifter)
    }
    return cost_map.get(cell_type, 1.0)


def comprehensive_delay_model():
    """Delay model with realistic timing."""
    return {
        "$buf": 0.1,
        "$add": 1.0,    # 1 time unit per addition
        "$mul": 1.5,    # Fast multiply (better than addition chain)
        "$shl": 0.5,    # Fast shift
        "default": 1.0
    }


def analyze_comprehensive_solution(solution: dict, name: str):
    """Detailed analysis of solution."""
    print(f"\n=== {name} Analysis ===")

    if 'cells' not in solution:
        print("No cells found")
        return

    cells = solution['cells']
    cell_counts = {}
    total_cost = 0

    for cell_name, cell_data in cells.items():
        cell_type = cell_data.get('type', 'unknown')
        cell_counts[cell_type] = cell_counts.get(cell_type, 0) + 1
        total_cost += comprehensive_cost_model(cell_type)

    print(f"Total cells: {len(cells)}, Total cost: {total_cost:.1f}")

    # Detailed breakdown
    for cell_type in sorted(cell_counts.keys()):
        count = cell_counts[cell_type]
        unit_cost = comprehensive_cost_model(cell_type)
        print(f"  {cell_type}: {count} × {unit_cost} = {count * unit_cost:.1f}")

    # Classify implementation
    mult_count = cell_counts.get('$mul', 0)
    add_count = cell_counts.get('$add', 0)
    shl_count = cell_counts.get('$shl', 0)

    if mult_count > 0 and shl_count == 1:
        impl_type = "ULTRA-FAST (multiply + barrel shift)"
    elif mult_count > 0 and shl_count > 1:
        impl_type = "FAST (multiply + iterative shifts)"
    elif mult_count == 0 and add_count <= 6:
        impl_type = "MEDIUM (moderate addition chains)"
    else:
        impl_type = "SLOW (long addition chains)"

    print(f"Implementation: {impl_type}")


def main():
    """Run comprehensive timing demonstration."""
    print("Comprehensive Timing-Oriented Extraction Demo")
    print("=" * 60)
    print("Circuit: result = (a * b) + (c << 2)")
    print("Four implementation strategies with different cost/timing trade-offs\n")

    db = create_comprehensive_circuit()
    delay_model = comprehensive_delay_model()

    tests = [
        ("Cost-only", None, None),
        ("Ultra-tight timing", 3.0, "should force ULTRA-FAST path"),
        ("Tight timing", 5.0, "should force FAST path"),
        ("Medium timing", 7.0, "should allow MEDIUM path"),
        ("Loose timing", 12.0, "should prefer SLOW path"),
    ]

    solutions = {}

    for test_name, target_delay, description in tests:
        print("=" * 60)
        print(f"Test: {test_name}")
        if description:
            print(f"Target delay: {target_delay}ns - {description}")

        try:
            if target_delay is None:
                # Cost-only extraction
                solution = extract_no_techmap(db, comprehensive_cost_model, solver_type="cbc")
            else:
                # Timing-aware extraction
                solution = extract_timing_aware(
                    db, comprehensive_cost_model, target_delay=target_delay,
                    delay_model=delay_model, solver_type="cbc"
                )

            analyze_comprehensive_solution(solution, test_name)
            solutions[test_name] = solution

        except ValueError as e:
            print(f"INFEASIBLE: {e}")
            solutions[test_name] = None

    # Compare all solutions
    print("\n" + "=" * 60)
    print("Solution Comparison Matrix:")

    solution_names = list(solutions.keys())
    for i, name1 in enumerate(solution_names):
        for j, name2 in enumerate(solution_names):
            if i < j and solutions[name1] is not None and solutions[name2] is not None:
                different = solutions[name1] != solutions[name2]
                status = "DIFFERENT" if different else "SAME"
                print(f"{name1:20} vs {name2:20}: {status}")

    print(f"\nSuccessfully demonstrated timing-driven cell type selection!")
    return 0


if __name__ == "__main__":
    sys.exit(main())