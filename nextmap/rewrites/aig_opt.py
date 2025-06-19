"""
AIG optimization using equality saturation rewrites.

This module implements AIG-level rewrites inspired by ABC's optimization passes,
but using equality saturation to accumulate equivalences and find optimizations
that pure structural rewriting can't discover.

IMPORTANT: Associativity/commutativity rules are carefully controlled to prevent
exponential explosion of the equivalence classes.
"""

from typing import Iterable, Set, Tuple, List, Dict
from ..db_aig import NetlistDB as AIGNetlistDB


# =============================================================================
# E-matching: Find patterns in the AIG
# =============================================================================

def ematch_and_and_left(db: AIGNetlistDB) -> Iterable[Tuple[int, int, int, int]]:
    """
    Match: AND(AND(a, b), c) -> return (a, b, c, output)

    Used for associativity rewrites, but carefully controlled.
    """
    cur = db.execute("""
        SELECT inner.a, inner.b, outer.b, outer.y
        FROM ands AS outer
        JOIN ands AS inner ON outer.a = inner.y
    """)
    return cur.fetchall()


def ematch_and_and_right(db: AIGNetlistDB) -> Iterable[Tuple[int, int, int, int]]:
    """
    Match: AND(a, AND(b, c)) -> return (a, b, c, output)
    """
    cur = db.execute("""
        SELECT outer.a, inner.a, inner.b, outer.y
        FROM ands AS outer
        JOIN ands AS inner ON outer.b = inner.y
    """)
    return cur.fetchall()


def ematch_and_inv(db: AIGNetlistDB) -> Iterable[Tuple[int, int, int]]:
    """
    Match: AND(a, INV(b)) -> return (a, b, output)

    Can be rewritten to NAND or other forms.
    """
    cur = db.execute("""
        SELECT outer.a, inner.a, outer.y
        FROM ands AS outer
        JOIN invs AS inner ON outer.b = inner.y
    """)
    return cur.fetchall()


def ematch_inv_inv(db: AIGNetlistDB) -> Iterable[Tuple[int, int]]:
    """
    Match: INV(INV(a)) -> return (a, output)

    Classic double-inversion elimination.
    """
    cur = db.execute("""
        SELECT inner.a, outer.y
        FROM invs AS outer
        JOIN invs AS inner ON outer.a = inner.y
    """)
    return cur.fetchall()


def ematch_and_same_input(db: AIGNetlistDB) -> Iterable[Tuple[int, int]]:
    """
    Match: AND(a, a) -> return (a, output)

    Idempotence: AND(x, x) = x
    """
    cur = db.execute("""
        SELECT a, y
        FROM ands
        WHERE a = b
    """)
    return cur.fetchall()


def ematch_and_with_constant(db: AIGNetlistDB) -> Iterable[Tuple[int, int, int]]:
    """
    Match: AND(a, 0) or AND(a, 1) -> return (a, constant, output)
    """
    cur = db.execute("""
        SELECT a, b, y
        FROM ands
        WHERE b IN (0, 1)
    """)
    return cur.fetchall()


def ematch_and_complementary(db: AIGNetlistDB) -> Iterable[Tuple[int, int, int]]:
    """
    Match: AND(a, INV(a)) -> return (a, inv_wire, output)

    Complementary inputs: AND(x, NOT(x)) = 0
    """
    cur = db.execute("""
        SELECT outer.a, inner.y, outer.y
        FROM ands AS outer
        JOIN invs AS inner ON outer.b = inner.y
        WHERE outer.a = inner.a
    """)
    return cur.fetchall()


def ematch_de_morgan_and(db: AIGNetlistDB) -> Iterable[Tuple[int, int, int, int]]:
    """
    Match: INV(AND(INV(a), INV(b))) -> return (a, b, inv_a, output)

    De Morgan's law: NOT(NOT(a) AND NOT(b)) = a OR b
    """
    cur = db.execute("""
        SELECT inv_a.a, inv_b.a, and_gate.y, outer_inv.y
        FROM invs AS outer_inv
        JOIN ands AS and_gate ON outer_inv.a = and_gate.y
        JOIN invs AS inv_a ON and_gate.a = inv_a.y
        JOIN invs AS inv_b ON and_gate.b = inv_b.y
    """)
    return cur.fetchall()


def ematch_common_subexpression(db: AIGNetlistDB) -> Iterable[Tuple[int, int, int, int]]:
    """
    Match: Two AND gates with same inputs -> return (a, b, y1, y2)

    Common subexpression elimination: if AND(a,b) computed twice, merge them.
    """
    cur = db.execute("""
        SELECT g1.a, g1.b, g1.y, g2.y
        FROM ands AS g1
        JOIN ands AS g2 ON g1.a = g2.a AND g1.b = g2.b
        WHERE g1.y < g2.y
    """)
    return cur.fetchall()


# =============================================================================
# Rewrite application: Apply rewrites to create new equivalences
# =============================================================================

def apply_inv_inv_elimination(db: AIGNetlistDB, matches: Iterable[Tuple[int, int]]) -> int:
    """
    Apply: INV(INV(a)) = a

    This is always beneficial - reduces gate count.
    """
    count = 0
    for a, output in matches:
        # Create equivalence: output ≡ a
        # In a pure e-graph, we'd union these nodes
        # Here we mark them as equivalent by inserting a rewrite
        db.execute("""
            INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
            VALUES (?, ?, 'inv_inv_elim')
        """, (output, a))
        count += 1

    if count > 0:
        db.commit()
    return count


def apply_and_idempotence(db: AIGNetlistDB, matches: Iterable[Tuple[int, int]]) -> int:
    """
    Apply: AND(a, a) = a
    """
    count = 0
    for a, output in matches:
        db.execute("""
            INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
            VALUES (?, ?, 'and_idempotent')
        """, (output, a))
        count += 1

    if count > 0:
        db.commit()
    return count


def apply_and_constant(db: AIGNetlistDB, matches: Iterable[Tuple[int, int, int]]) -> int:
    """
    Apply: AND(a, 0) = 0, AND(a, 1) = a
    """
    count = 0
    for a, const, output in matches:
        if const == 0:
            # AND(a, 0) = 0
            db.execute("""
                INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
                VALUES (?, 0, 'and_zero')
            """, (output,))
            count += 1
        elif const == 1:
            # AND(a, 1) = a
            db.execute("""
                INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
                VALUES (?, ?, 'and_one')
            """, (output, a))
            count += 1

    if count > 0:
        db.commit()
    return count


def apply_and_complement(db: AIGNetlistDB, matches: Iterable[Tuple[int, int, int]]) -> int:
    """
    Apply: AND(a, INV(a)) = 0
    """
    count = 0
    for a, inv_a, output in matches:
        db.execute("""
            INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
            VALUES (?, 0, 'and_complement')
        """, (output,))
        count += 1

    if count > 0:
        db.commit()
    return count


def apply_common_subexpression(db: AIGNetlistDB, matches: Iterable[Tuple[int, int, int, int]]) -> int:
    """
    Apply: If AND(a, b) computed twice, they're equivalent.

    This is CSE (Common Subexpression Elimination).
    """
    count = 0
    for a, b, y1, y2 in matches:
        db.execute("""
            INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
            VALUES (?, ?, 'cse_and')
        """, (y1, y2))
        count += 1

    if count > 0:
        db.commit()
    return count


def apply_associativity_limited(db: AIGNetlistDB,
                                 left_matches: Iterable[Tuple[int, int, int, int]],
                                 right_matches: Iterable[Tuple[int, int, int, int]],
                                 max_rewrites: int = 100) -> int:
    """
    Apply associativity rewrites with strict limits to prevent explosion.

    AND(AND(a, b), c) ≡ AND(a, AND(b, c))

    IMPORTANT: We only apply this rewrite when it's likely to help:
    1. Limit total number of associativity rewrites per iteration
    2. Only apply when it creates opportunities for other optimizations
    3. Prefer canonical forms (e.g., sort by wire ID to reduce redundancy)

    Args:
        max_rewrites: Maximum associativity rewrites per call (default 100)
    """
    count = 0

    # Strategy: Only apply associativity when it helps with other rewrites
    # For example, if we have AND(AND(a, b), a), rewrite to AND(a, AND(b, a))
    # which then becomes AND(a, AND(a, b)) -> opportunities for CSE

    for a, b, c, output in left_matches:
        if count >= max_rewrites:
            break

        # Check if applying associativity would create new opportunities
        # For now, only apply if it brings the same variable together
        if a == c or b == c:
            # This could enable absorption or idempotence
            # Create the alternative form: AND(a, AND(b, c))
            # We don't actually create new gates, just mark the equivalence
            db.execute("""
                INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
                VALUES (?, ?, 'assoc_opportunity')
            """, (output, output))  # Placeholder - needs proper handling
            count += 1

    if count > 0:
        db.commit()

    return count


# =============================================================================
# Equivalence canonicalization
# =============================================================================

def build_equivalence_classes(db: AIGNetlistDB) -> dict:
    """
    Build equivalence classes from wire_equivalences table using union-find.

    Returns:
        Dictionary mapping wire_id -> canonical_wire_id
    """
    from ..utils import DisjointSetUnion

    # Get all wires in the AIG
    cur = db.execute("SELECT DISTINCT a FROM ands UNION SELECT DISTINCT b FROM ands UNION SELECT DISTINCT y FROM ands")
    all_wires = {w for (w,) in cur.fetchall()}

    cur = db.execute("SELECT DISTINCT a FROM invs UNION SELECT DISTINCT y FROM invs")
    all_wires.update(w for (w,) in cur.fetchall())

    # Initialize union-find (DisjointSetUnion auto-initializes on first find)
    dsu = DisjointSetUnion()

    # Union based on equivalences
    cur = db.execute("SELECT wire1, wire2 FROM wire_equivalences")
    for w1, w2 in cur.fetchall():
        dsu.union(w1, w2)

    # Build canonical mapping
    canonical = {}
    for w in all_wires:
        canonical[w] = dsu.find(w)

    return canonical


def canonicalize_aig(db: AIGNetlistDB, canonical: dict) -> tuple:
    """
    Rebuild AIG using canonical wire representatives.

    This is the "rebuild" step of equality saturation.

    Returns:
        Tuple of (ands_removed, invs_removed)
    """
    # Rebuild ANDs with canonical wires
    cur = db.execute("SELECT a, b, y FROM ands")
    old_ands = cur.fetchall()

    # Clear old gates (we'll rebuild)
    db.execute("DELETE FROM ands")
    db.execute("DELETE FROM invs")

    # Rebuild ANDs
    new_ands = set()
    ands_removed = 0
    for a, b, y in old_ands:
        # Map to canonical representatives
        canon_a = canonical.get(a, a)
        canon_b = canonical.get(b, b)
        canon_y = canonical.get(y, y)

        # Normalize: ensure a <= b for canonicalization
        if canon_a > canon_b:
            canon_a, canon_b = canon_b, canon_a

        # Check for trivial cases after canonicalization
        if canon_a == canon_y or (canon_a, canon_b, canon_y) in new_ands:
            ands_removed += 1
            continue

        new_ands.add((canon_a, canon_b, canon_y))

    # Insert new canonical ANDs
    db.executemany("INSERT OR IGNORE INTO ands (a, b, y) VALUES (?, ?, ?)", new_ands)

    # Rebuild INVs (similar process)
    cur = db.execute("SELECT a, y FROM invs")
    old_invs = cur.fetchall()

    new_invs = set()
    invs_removed = 0
    for a, y in old_invs:
        canon_a = canonical.get(a, a)
        canon_y = canonical.get(y, y)

        # Check for trivial inverter: INV(a) where a = y after canonicalization
        if canon_a == canon_y or (canon_a, canon_y) in new_invs:
            invs_removed += 1
            continue

        new_invs.add((canon_a, canon_y))

    db.executemany("INSERT OR IGNORE INTO invs (a, y) VALUES (?, ?)", new_invs)

    # Update as_outputs table with canonical wire IDs
    cur = db.execute("SELECT sink, name FROM as_outputs")
    old_outputs = cur.fetchall()
    db.execute("DELETE FROM as_outputs")

    for old_sink, name in old_outputs:
        canon_sink = canonical.get(old_sink, old_sink)
        db.execute("INSERT OR IGNORE INTO as_outputs (sink, name) VALUES (?, ?)", (canon_sink, name))

    # Update from_inputs table with canonical wire IDs
    cur = db.execute("SELECT source FROM from_inputs")
    old_inputs = [row[0] for row in cur.fetchall()]
    db.execute("DELETE FROM from_inputs")

    for old_source in old_inputs:
        canon_source = canonical.get(old_source, old_source)
        db.execute("INSERT OR IGNORE INTO from_inputs (source) VALUES (?)", (canon_source,))

    db.commit()
    return (ands_removed, invs_removed)


# =============================================================================
# AIG Balancing (inspired by ABC's darBalance)
# =============================================================================

def compute_wire_levels(db: AIGNetlistDB) -> Dict[int, int]:
    """
    Compute the level (depth) of each wire in the AIG.

    Level 0: Primary inputs
    Level n: Max(level(fanin)) + 1

    Returns:
        Dictionary mapping wire_id -> level
    """
    # Get all wires
    cur = db.execute("SELECT DISTINCT a FROM ands UNION SELECT DISTINCT b FROM ands UNION SELECT DISTINCT y FROM ands")
    all_wires = set(w for (w,) in cur.fetchall())

    cur = db.execute("SELECT DISTINCT a FROM invs UNION SELECT DISTINCT y FROM invs")
    all_wires.update(w for (w,) in cur.fetchall())

    # Get primary inputs (from from_inputs table)
    cur = db.execute("SELECT source FROM from_inputs")
    inputs = set(w for (w,) in cur.fetchall())

    # Build fanin map
    fanins = {}  # wire_id -> list of fanin wire_ids

    cur = db.execute("SELECT a, b, y FROM ands")
    for a, b, y in cur.fetchall():
        fanins[y] = [a, b]

    cur = db.execute("SELECT a, y FROM invs")
    for a, y in cur.fetchall():
        fanins[y] = [a]

    # Compute levels using topological sort
    levels = {}

    # Set input levels to 0
    for inp in inputs:
        levels[inp] = 0

    # Constants
    levels[0] = 0
    levels[1] = 0

    # Iteratively compute levels
    changed = True
    max_iters = 1000
    iters = 0
    while changed and iters < max_iters:
        changed = False
        iters += 1

        for wire in all_wires:
            if wire in levels:
                continue  # Already computed

            if wire not in fanins:
                # No fanins - treat as input
                levels[wire] = 0
                changed = True
                continue

            # Check if all fanins have levels
            fanin_list = fanins[wire]
            if all(f in levels for f in fanin_list):
                # Compute level as max(fanin levels) + 1
                levels[wire] = max(levels[f] for f in fanin_list) + 1
                changed = True

    return levels


def collect_supergate(db: AIGNetlistDB, root: int, is_and: bool,
                       fanout_counts: Dict[int, int],
                       max_size: int = 1000) -> List[int]:
    """
    Collect a 'supergate' - flatten chain of AND gates into a list.

    Similar to ABC's Dar_BalanceCone_rec().

    A supergate is a maximal chain of gates of the same type where each
    internal node has fanout = 1.

    Args:
        db: AIG database
        root: Starting wire ID
        is_and: True if collecting AND gates, False if collecting OR gates
        fanout_counts: Precomputed fanout counts for each wire
        max_size: Stop if supergate exceeds this size

    Returns:
        List of leaf wire IDs (inputs to the supergate)
    """
    # Build lookup tables
    and_gates = {}  # y -> (a, b)
    inv_gates = {}   # y -> a

    cur = db.execute("SELECT a, b, y FROM ands")
    for a, b, y in cur.fetchall():
        and_gates[y] = (a, b)

    cur = db.execute("SELECT a, y FROM invs")
    for a, y in cur.fetchall():
        inv_gates[y] = a

    supergate = []

    def collect_rec(wire: int, depth: int = 0):
        """Recursively collect supergate leaves."""
        if len(supergate) >= max_size:
            # Stop if too large
            return

        # Stop conditions:
        # 1. Wire has high fanout (> 1) and is not the root
        # 2. Wire is not an AND gate (when collecting ANDs)
        # 3. Wire is an inverter output (inversion breaks the chain)

        if wire != root and fanout_counts.get(wire, 0) > 1:
            # High-fanout node - treat as leaf
            supergate.append(wire)
            return

        # Check if this is an AND gate output
        if wire in and_gates and is_and:
            # It's an AND gate - recurse into children
            a, b = and_gates[wire]
            collect_rec(a, depth + 1)
            collect_rec(b, depth + 1)
        else:
            # Not an AND gate, or wrong type - treat as leaf
            supergate.append(wire)

    collect_rec(root)
    return supergate


def compute_fanout_counts(db: AIGNetlistDB) -> Dict[int, int]:
    """
    Compute fanout count for each wire in the AIG.

    Returns:
        Dictionary mapping wire_id -> fanout_count
    """
    fanouts = {}

    # Count uses in AND gates
    cur = db.execute("SELECT a FROM ands UNION ALL SELECT b FROM ands")
    for (wire,) in cur.fetchall():
        fanouts[wire] = fanouts.get(wire, 0) + 1

    # Count uses in INV gates
    cur = db.execute("SELECT a FROM invs")
    for (wire,) in cur.fetchall():
        fanouts[wire] = fanouts.get(wire, 0) + 1

    # Count uses as outputs
    cur = db.execute("SELECT sink FROM as_outputs")
    for (wire,) in cur.fetchall():
        fanouts[wire] = fanouts.get(wire, 0) + 1

    return fanouts


def build_balanced_tree(db: AIGNetlistDB, leaves: List[int], levels: Dict[int, int],
                         next_wire_id: int) -> Tuple[int, int]:
    """
    Build a balanced AND tree from a list of leaves.

    Args:
        db: AIG database
        leaves: List of input wires
        levels: Wire level map (for sorting)
        next_wire_id: Next available wire ID for new gates

    Returns:
        Tuple of (output_wire_id, next_wire_id)
    """
    if len(leaves) == 0:
        # No leaves - return constant 1
        return (1, next_wire_id)

    if len(leaves) == 1:
        # Single leaf - return it directly
        return (leaves[0], next_wire_id)

    # Remove duplicates and sort by level (lowest level first)
    unique_leaves = list(set(leaves))
    unique_leaves.sort(key=lambda w: levels.get(w, 0))

    # Check for a & ~a = 0 (complementary inputs)
    # Build a set of inverted wires
    cur = db.execute("SELECT a, y FROM invs")
    inverted = {}  # a -> inv(a)
    for a, y in cur.fetchall():
        inverted[a] = y

    # Check for contradictions
    for leaf in unique_leaves:
        if leaf in inverted and inverted[leaf] in unique_leaves:
            # Found a & ~a - return 0
            return (0, next_wire_id)

    # Build balanced tree by repeatedly pairing adjacent elements
    current_level = unique_leaves

    while len(current_level) > 1:
        next_level = []

        # Pair up elements
        for i in range(0, len(current_level), 2):
            if i + 1 < len(current_level):
                # Create AND gate for pair
                a, b = current_level[i], current_level[i + 1]

                # Normalize: a <= b
                if a > b:
                    a, b = b, a

                # Check if this AND already exists
                cur = db.execute("SELECT y FROM ands WHERE a = ? AND b = ?", (a, b))
                existing = cur.fetchone()

                if existing:
                    output = existing[0]
                else:
                    # Create new AND gate
                    output = next_wire_id
                    next_wire_id += 1
                    db.execute("INSERT INTO ands (a, b, y) VALUES (?, ?, ?)", (a, b, output))

                next_level.append(output)
            else:
                # Odd element - carry forward
                next_level.append(current_level[i])

        current_level = next_level

    return (current_level[0], next_wire_id)


def balance_aig(db: AIGNetlistDB, verbose: bool = False) -> Dict:
    """
    Balance the AIG by restructuring AND gate trees.

    This is inspired by ABC's darBalance algorithm. It:
    1. Identifies chains of AND gates (supergates)
    2. Flattens them into a list of inputs
    3. Rebuilds as balanced binary trees to minimize depth

    Benefits:
    - Reduces logic depth
    - Creates more regular structures for LUT mapping
    - Can expose optimization opportunities

    Returns:
        Dictionary with statistics
    """
    stats = {
        'supergates_balanced': 0,
        'max_supergate_size': 0,
        'total_leaves': 0,
        'ands_before': 0,
        'ands_after': 0
    }

    # Count initial ANDs
    cur = db.execute("SELECT COUNT(*) FROM ands")
    stats['ands_before'] = cur.fetchone()[0]

    if verbose:
        print(f"\n=== AIG Balancing ===")
        print(f"  Initial AND gates: {stats['ands_before']}")

    # Compute wire levels
    levels = compute_wire_levels(db)

    # Compute fanout counts
    fanout_counts = compute_fanout_counts(db)

    # Find all AND gate outputs (potential supergate roots)
    cur = db.execute("SELECT y FROM ands")
    and_outputs = [y for (y,) in cur.fetchall()]

    # Also check outputs of the design
    cur = db.execute("SELECT sink FROM as_outputs")
    design_outputs = set(s for (s,) in cur.fetchall())

    # Track which wires have been processed
    processed = set()

    # Collect all AND gates to remove
    gates_to_remove = []

    # New AND gates to add
    new_ands = []

    # Get next available wire ID
    cur = db.execute("SELECT MAX(y) FROM ands")
    max_wire = cur.fetchone()[0]
    next_wire_id = (max_wire // 10000 + 1) * 10000  # Start at next 10k boundary

    # Wire replacement map: old_wire -> new_wire
    replacements = {}

    # Debug: Sample a few AND outputs to see what's happening
    if verbose and len(and_outputs) > 0:
        print(f"  Total AND outputs to consider: {len(and_outputs)}")
        print(f"  Design outputs: {len(design_outputs)}")
        print(f"  Sampling first 10 AND gates for diagnostics:")
        for i, and_out in enumerate(and_outputs[:10]):
            fanout = fanout_counts.get(and_out, 0)
            is_output = and_out in design_outputs
            print(f"    Wire {and_out}: fanout={fanout}, is_output={is_output}")
            # Try collecting supergate for diagnostics
            test_supergate = collect_supergate(db, and_out, is_and=True,
                                               fanout_counts=fanout_counts, max_size=1000)
            print(f"      -> Supergate size: {len(test_supergate)}")

    # Balance each supergate
    skipped_dead = 0
    skipped_small = 0
    for and_output in and_outputs:
        if and_output in processed:
            continue

        # Skip dead code (unused wires)
        fanout = fanout_counts.get(and_output, 0)
        if fanout == 0 and and_output not in design_outputs:
            skipped_dead += 1
            continue

        # Collect supergate
        supergate = collect_supergate(db, and_output, is_and=True,
                                       fanout_counts=fanout_counts, max_size=1000)

        if len(supergate) <= 2:
            # Too small to benefit from balancing
            skipped_small += 1
            continue

        stats['supergates_balanced'] += 1
        stats['max_supergate_size'] = max(stats['max_supergate_size'], len(supergate))
        stats['total_leaves'] += len(supergate)

        if verbose and stats['supergates_balanced'] <= 10:  # Show first 10
            print(f"  Supergate {stats['supergates_balanced']}: {len(supergate)} leaves")

        # Mark all wires in this supergate as processed
        # We need to traverse the tree to find all intermediate wires
        def mark_processed(wire):
            if wire in processed:
                return
            processed.add(wire)

            # Check if it's an AND gate
            cur = db.execute("SELECT a, b FROM ands WHERE y = ?", (wire,))
            row = cur.fetchone()
            if row:
                a, b = row
                mark_processed(a)
                mark_processed(b)

        mark_processed(and_output)

        # Build balanced tree
        new_output, next_wire_id = build_balanced_tree(db, supergate, levels, next_wire_id)

        # Record replacement
        if new_output != and_output:
            replacements[and_output] = new_output

    if verbose:
        print(f"  Skipped {skipped_dead} dead AND gates (fanout=0)")
        print(f"  Skipped {skipped_small} small supergates (size <= 2)")
        print(f"  Balanced {stats['supergates_balanced']} supergates")
        print(f"  Max supergate size: {stats['max_supergate_size']}")
        print(f"  Created {len(replacements)} wire replacements")

    # Apply replacements to all gates
    if replacements:
        # Update AND gates
        cur = db.execute("SELECT a, b, y FROM ands")
        old_ands = cur.fetchall()
        db.execute("DELETE FROM ands")

        for a, b, y in old_ands:
            new_a = replacements.get(a, a)
            new_b = replacements.get(b, b)
            new_y = replacements.get(y, y)

            # Normalize
            if new_a > new_b:
                new_a, new_b = new_b, new_a

            db.execute("INSERT OR IGNORE INTO ands (a, b, y) VALUES (?, ?, ?)", (new_a, new_b, new_y))

        # Update INV gates
        cur = db.execute("SELECT a, y FROM invs")
        old_invs = cur.fetchall()
        db.execute("DELETE FROM invs")

        for a, y in old_invs:
            new_a = replacements.get(a, a)
            new_y = replacements.get(y, y)
            db.execute("INSERT OR IGNORE INTO invs (a, y) VALUES (?, ?)", (new_a, new_y))

        # Update outputs
        cur = db.execute("SELECT sink, name FROM as_outputs")
        old_outputs = cur.fetchall()
        db.execute("DELETE FROM as_outputs")

        for sink, name in old_outputs:
            new_sink = replacements.get(sink, sink)
            db.execute("INSERT OR IGNORE INTO as_outputs (sink, name) VALUES (?, ?)", (new_sink, name))

        db.commit()

    # Count final ANDs
    cur = db.execute("SELECT COUNT(*) FROM ands")
    stats['ands_after'] = cur.fetchone()[0]

    if verbose:
        print(f"  Final AND gates: {stats['ands_after']}")
        print(f"  Change: {stats['ands_after'] - stats['ands_before']:+d}")

    return stats


# =============================================================================
# Simulation-based functional equivalence (inspired by FRAIG)
# =============================================================================

def simulate_aig_sql(db: AIGNetlistDB, num_patterns: int = 64, verbose: bool = False) -> int:
    """
    Simulate the AIG with random patterns using SQL-based computation.

    Much faster than Python loops! Uses SQL bitwise operations to propagate
    simulation signatures through the circuit.

    Args:
        db: AIG database
        num_patterns: Number of random patterns (max 64 for INTEGER storage)
        verbose: Print debug info

    Returns:
        Number of wires simulated
    """
    import random

    if verbose:
        print(f"\n=== SQL-based simulation with {num_patterns} patterns ===")

    # Create simulation table
    db.execute("DROP TABLE IF EXISTS wire_sims")
    db.execute("""
        CREATE TABLE wire_sims (
            wire_id INTEGER PRIMARY KEY,
            signature INTEGER
        )
    """)

    # Assign random signatures to primary inputs
    cur = db.execute("SELECT source FROM from_inputs")
    inputs = [w for (w,) in cur.fetchall()]

    for inp in inputs:
        # Generate random signature (limit to avoid overflow)
        if num_patterns >= 63:
            sig = random.getrandbits(63)  # SQLite INTEGER is signed 64-bit
        else:
            sig = random.getrandbits(num_patterns)
        db.execute("INSERT OR REPLACE INTO wire_sims (wire_id, signature) VALUES (?, ?)", (inp, sig))

    # Constants
    db.execute("INSERT OR REPLACE INTO wire_sims (wire_id, signature) VALUES (0, 0)")  # 0 = all zeros
    # For constant 1, create all-ones pattern (limited to avoid overflow)
    all_ones = (1 << min(num_patterns, 63)) - 1
    db.execute("INSERT OR REPLACE INTO wire_sims (wire_id, signature) VALUES (1, ?)", (all_ones,))

    db.commit()

    if verbose:
        print(f"  Initialized {len(inputs)} primary inputs with random signatures")

    # Iteratively propagate signatures through gates (topological order)
    max_iters = 100
    for iter_num in range(max_iters):
        # Propagate through AND gates
        # sig(y) = sig(a) & sig(b)
        cur = db.execute("""
            INSERT OR IGNORE INTO wire_sims (wire_id, signature)
            SELECT ands.y, (sa.signature & sb.signature)
            FROM ands
            JOIN wire_sims sa ON ands.a = sa.wire_id
            JOIN wire_sims sb ON ands.b = sb.wire_id
            WHERE ands.y NOT IN (SELECT wire_id FROM wire_sims)
        """)
        and_added = cur.rowcount

        # Propagate through INV gates
        # sig(y) = ~sig(a)
        # Note: Need to mask to num_patterns bits (max 63 for SQLite)
        mask = (1 << min(num_patterns, 63)) - 1
        cur = db.execute(f"""
            INSERT OR IGNORE INTO wire_sims (wire_id, signature)
            SELECT invs.y, ((~sa.signature) & {mask})
            FROM invs
            JOIN wire_sims sa ON invs.a = sa.wire_id
            WHERE invs.y NOT IN (SELECT wire_id FROM wire_sims)
        """)
        inv_added = cur.rowcount

        db.commit()

        if verbose and iter_num < 5:
            print(f"  Iteration {iter_num + 1}: propagated {and_added} ANDs, {inv_added} INVs")

        # Stop if no new wires were simulated
        if and_added == 0 and inv_added == 0:
            if verbose:
                print(f"  Converged after {iter_num + 1} iterations")
            break

    # Count simulated wires
    cur = db.execute("SELECT COUNT(*) FROM wire_sims")
    wire_count = cur.fetchone()[0]

    if verbose:
        print(f"  Simulated {wire_count} wires total")

    return wire_count


def find_simulation_equivalences(db: AIGNetlistDB, num_patterns: int = 64,
                                   verbose: bool = False) -> int:
    """
    Find functionally equivalent wires using SQL-based random simulation.

    This creates wire_equivalence entries for wires that have identical
    simulation signatures.

    Returns:
        Number of equivalences found
    """
    # Run SQL-based simulation
    simulate_aig_sql(db, num_patterns, verbose)

    # Find groups of wires with identical signatures (pure SQL!)
    cur = db.execute("""
        SELECT signature, GROUP_CONCAT(wire_id) as wires, COUNT(*) as cnt
        FROM wire_sims
        GROUP BY signature
        HAVING cnt > 1
    """)

    equiv_count = 0
    large_groups = 0

    for sig, wires_str, cnt in cur.fetchall():
        large_groups += 1
        wires = [int(w) for w in wires_str.split(',')]

        # Use smallest wire ID as canonical
        canonical = min(wires)

        # Create equivalences
        for wire in wires:
            if wire != canonical:
                db.execute("""
                    INSERT OR IGNORE INTO wire_equivalences (wire1, wire2, rule)
                    VALUES (?, ?, 'sim_equiv')
                """, (wire, canonical))
                equiv_count += 1

    if equiv_count > 0:
        db.commit()

    if verbose:
        print(f"  Found {large_groups} signature groups with multiple wires")
        print(f"  Created {equiv_count} simulation-based equivalences")

    # Clean up simulation table
    db.execute("DROP TABLE IF EXISTS wire_sims")
    db.commit()

    return equiv_count


# =============================================================================
# Main optimization loop
# =============================================================================

def optimize_aig_with_eqsat(db: AIGNetlistDB, max_iterations: int = 5,
                             enable_associativity: bool = False,
                             enable_simulation: bool = True,
                             verbose: bool = False) -> dict:
    """
    Optimize AIG using equality saturation.

    This runs multiple iterations of:
    1. E-matching: Find all pattern matches
    2. Apply rewrites: Create equivalences
    3. Simulation: Find functional equivalences (FRAIG-style)
    4. Rebuild: Canonicalize based on equivalences

    Args:
        db: AIG NetlistDB
        max_iterations: Maximum number of iterations
        enable_associativity: Enable associativity rewrites (can be expensive!)
        enable_simulation: Enable simulation-based equivalence (FRAIG-style)

    Returns:
        Dictionary with statistics
    """
    stats = {
        'iterations': 0,
        'total_rewrites': 0,
        'inv_inv_elim': 0,
        'and_idempotent': 0,
        'and_constant': 0,
        'and_complement': 0,
        'cse': 0,
        'associativity': 0,
        'simulation': 0
    }

    # Create equivalence table if it doesn't exist
    db.execute("""
        CREATE TABLE IF NOT EXISTS wire_equivalences (
            wire1 INTEGER,
            wire2 INTEGER,
            rule TEXT,
            PRIMARY KEY (wire1, wire2)
        )
    """)
    db.commit()

    for iteration in range(max_iterations):
        rewrites_this_iter = 0

        if verbose:
            print(f"\n=== Iteration {iteration + 1} ===")

        # 1. Double inversion elimination (always safe and beneficial)
        matches = list(ematch_inv_inv(db))
        if matches:
            count = apply_inv_inv_elimination(db, matches)
            stats['inv_inv_elim'] += count
            rewrites_this_iter += count
            if verbose:
                print(f"  INV(INV) elimination: {count} rewrites")

        # 2. Idempotence: AND(x, x) = x
        matches = list(ematch_and_same_input(db))
        if matches:
            count = apply_and_idempotence(db, matches)
            stats['and_idempotent'] += count
            rewrites_this_iter += count
            if verbose:
                print(f"  AND(x,x) idempotence: {count} rewrites")

        # 3. AND with constants
        matches = list(ematch_and_with_constant(db))
        if matches:
            count = apply_and_constant(db, matches)
            stats['and_constant'] += count
            rewrites_this_iter += count
            if verbose:
                print(f"  AND with constants: {count} rewrites")

        # 4. Complementary inputs: AND(x, NOT(x)) = 0
        matches = list(ematch_and_complementary(db))
        if matches:
            count = apply_and_complement(db, matches)
            stats['and_complement'] += count
            rewrites_this_iter += count
            if verbose:
                print(f"  AND(x, NOT(x)) complement: {count} rewrites")

        # 5. Common subexpression elimination
        matches = list(ematch_common_subexpression(db))
        if matches:
            count = apply_common_subexpression(db, matches)
            stats['cse'] += count
            rewrites_this_iter += count
            if verbose:
                print(f"  Common subexpression elimination: {count} rewrites")

        # 6. Simulation-based functional equivalence (FRAIG-style)
        if enable_simulation and iteration == 0:  # Only run once after first CSE pass
            count = find_simulation_equivalences(db, num_patterns=64, verbose=verbose)
            stats['simulation'] += count
            rewrites_this_iter += count
            if verbose and count > 0:
                print(f"  Simulation-based equivalences: {count}")

        # 7. Associativity (optional, controlled)
        if enable_associativity:
            left_matches = list(ematch_and_and_left(db))
            right_matches = list(ematch_and_and_right(db))
            if left_matches or right_matches:
                count = apply_associativity_limited(db, left_matches, right_matches, max_rewrites=50)
                stats['associativity'] += count
                rewrites_this_iter += count
                if verbose:
                    print(f"  Associativity rewrites: {count}")

        stats['iterations'] += 1
        stats['total_rewrites'] += rewrites_this_iter

        if verbose:
            print(f"  Total rewrites this iteration: {rewrites_this_iter}")

        # REBUILD step: Canonicalize the AIG based on accumulated equivalences
        if rewrites_this_iter > 0:
            canonical = build_equivalence_classes(db)
            ands_removed, invs_removed = canonicalize_aig(db, canonical)

            if verbose:
                print(f"  Canonicalization: removed {ands_removed} ANDs, {invs_removed} INVs")

            # Clear equivalences for next iteration (they've been applied)
            db.execute("DELETE FROM wire_equivalences")
            db.commit()

        # Stop if no rewrites were applied
        if rewrites_this_iter == 0:
            if verbose:
                print("  No more rewrites found, stopping.")
            break

    # Final statistics
    cur = db.execute("SELECT COUNT(*) FROM ands")
    stats['final_ands'] = cur.fetchone()[0]

    cur = db.execute("SELECT COUNT(*) FROM invs")
    stats['final_invs'] = cur.fetchone()[0]

    return stats
