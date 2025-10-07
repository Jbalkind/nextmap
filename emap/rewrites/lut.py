import random
from ..db import NetlistDB


def build_fanout_cache(netlist: NetlistDB) -> dict[int, int]:
    """Build a cache of fanout counts for all wires."""
    fanout_cache: dict[int, int] = {}

    # Count fanouts from AND gates
    cur = netlist.execute("SELECT a, b FROM ands")
    for a, b in cur.fetchall():
        fanout_cache[a] = fanout_cache.get(a, 0) + 1
        fanout_cache[b] = fanout_cache.get(b, 0) + 1

    # Count fanouts from inverters
    cur = netlist.execute("SELECT a FROM invs")
    for (a,) in cur.fetchall():
        fanout_cache[a] = fanout_cache.get(a, 0) + 1

    return fanout_cache


def build_input_graph(netlist: NetlistDB) -> tuple[dict[int, list[tuple]], set[int]]:
    """Build a reverse graph for efficient cone building."""
    inputs_of: dict[int, list[tuple]] = {}

    # Get primary inputs
    cur = netlist.execute("SELECT source FROM from_inputs")
    primary_inputs = {row[0] for row in cur.fetchall()}

    # Build reverse graph for AND gates
    cur = netlist.execute("SELECT a, b, y FROM ands")
    for a, b, y in cur.fetchall():
        if y not in inputs_of:
            inputs_of[y] = []
        inputs_of[y].append(('and', a, b))

    # Build reverse graph for inverters
    cur = netlist.execute("SELECT a, y FROM invs")
    for a, y in cur.fetchall():
        if y not in inputs_of:
            inputs_of[y] = []
        inputs_of[y].append(('inv', a))

    return inputs_of, primary_inputs


def find_cone(netlist: NetlistDB, k: int, w: int,
              fanout_cache: dict[int, int],
              inputs_of: dict[int, list[tuple]],
              primary_inputs: set[int]) -> set[int]:
    """Find a k-input cone for wire w using cached data structures."""
    cone: set[int] = {w}

    # BFS from w with cached lookups
    while len(cone) < k:
        # Find all choices at the frontier using cached graph
        and_choices = []
        inv_choices = []

        for wire in cone:
            if wire in inputs_of:
                for cell in inputs_of[wire]:
                    if cell[0] == 'and':
                        and_choices.append((cell[1], cell[2], wire))
                    else:  # inv
                        inv_choices.append((cell[1], wire))

        # Choose one with the smallest fanout using cached values
        best_choice, best_fanout = None, float("inf")

        for a, b, y in and_choices:
            # Skip if already in cone
            if a in cone and b in cone:
                continue
            # Calculate fanout cost
            fanout = fanout_cache.get(a, 0) + fanout_cache.get(b, 0)
            if fanout < best_fanout:
                best_choice = (a, b, y)
                best_fanout = fanout

        for a, y in inv_choices:
            if a in cone:
                continue
            fanout = fanout_cache.get(a, 0) * 2  # weight inverter more
            if fanout < best_fanout:
                best_choice = (a, y)
                best_fanout = fanout

        if best_choice is None:
            break

        if len(best_choice) == 3:   # choose an AND cell
            a, b, y = best_choice
            # Check if adding both inputs exceeds k
            new_inputs = (a not in cone) + (b not in cone)
            if len(cone) + new_inputs - 1 > k:
                break
            cone.remove(y)
            cone.add(a)
            cone.add(b)
        else:   # choose an inverter
            a, y = best_choice
            cone.remove(y)
            cone.add(a)

    return cone

def compute_depth_from_inputs(netlist: NetlistDB,
                              inputs_of: dict[int, list[tuple]],
                              primary_inputs: set[int]) -> dict[int, int]:
    """Compute topological depth from primary inputs for each wire."""
    depth: dict[int, int] = {}

    # Primary inputs have depth 0
    for inp in primary_inputs:
        depth[inp] = 0

    # Constants also have depth 0
    depth[0] = 0
    depth[1] = 0

    # BFS to compute depths
    changed = True
    max_iterations = 1000
    iteration = 0
    while changed and iteration < max_iterations:
        changed = False
        iteration += 1
        for wire, inputs in inputs_of.items():
            if wire in depth:
                continue
            # Check if all inputs have depths computed
            max_input_depth = -1
            all_inputs_ready = True
            for cell in inputs:
                if cell[0] == 'and':
                    a, b = cell[1], cell[2]
                    if a not in depth or b not in depth:
                        all_inputs_ready = False
                        break
                    max_input_depth = max(max_input_depth, depth[a], depth[b])
                else:  # inv
                    a = cell[1]
                    if a not in depth:
                        all_inputs_ready = False
                        break
                    max_input_depth = max(max_input_depth, depth[a])

            if all_inputs_ready and max_input_depth >= 0:
                depth[wire] = max_input_depth + 1
                changed = True

    return depth


def techmap_luts(netlist: NetlistDB, k: int, cnt: int, rseed: int, greedy: bool = True, verbose: bool = False):
    """
    Techmap the netlist with cnt k-LUTs using improved heuristics.

    Args:
        netlist: The netlist database
        k: LUT input size
        cnt: Maximum number of LUTs to create
        rseed: Random seed for reproducibility
        greedy: If True, use greedy maximal coverage. If False, use weighted random sampling.
        verbose: Enable verbose output
    """
    if verbose:
        mode = "greedy coverage" if greedy else "weighted random"
        print(f"Techmapping to {k}-LUTs with random seed {rseed} (mode: {mode})")

    # Build cached data structures once
    fanout_cache = build_fanout_cache(netlist)
    inputs_of, primary_inputs = build_input_graph(netlist)
    depth_map = compute_depth_from_inputs(netlist, inputs_of, primary_inputs)

    if verbose:
        print(f"Built data structures:")
        print(f"  Fanout cache: {len(fanout_cache)} wires")
        print(f"  Input graph: {len(inputs_of)} outputs, {len(primary_inputs)} primary inputs")
        print(f"  Depth map: {len(depth_map)} wires, max depth = {max(depth_map.values()) if depth_map else 0}")

    # Build candidate set: only gate outputs (from inputs_of.keys())
    # These are wires that are produced by gates, not primary inputs
    candidates: dict[int, float] = {}
    for wire in inputs_of.keys():
        if wire not in {0, 1}:
            fanout = fanout_cache.get(wire, 0)
            wire_depth = depth_map.get(wire, 0)
            # Score = fanout * (1 + depth/10) to prioritize both high fanout and deep logic
            score = fanout * (1.0 + wire_depth / 10.0)
            if score > 0:  # Only add if has fanout or depth
                candidates[wire] = score

    if not candidates:
        if verbose:
            print("No candidate wires found for LUT mapping")
        return

    random.seed(rseed)

    if greedy:
        # Greedy algorithm: repeatedly pick the highest-scoring uncovered wire
        # Track all wires covered by LUTs (both outputs and inputs in cones)
        covered_wires: set[int] = set()
        luts_created = 0

        while luts_created < cnt and candidates:
            # Find the best uncovered candidate
            # Adjust score based on how much new logic it covers
            best_wire = None
            best_adjusted_score = -1

            for wire, base_score in candidates.items():
                if wire in covered_wires:
                    continue  # Skip already covered outputs

                # Quick estimate: count how many new wires this would cover
                # Full cone building is expensive, so we use a heuristic
                # Penalize wires whose inputs are already heavily covered
                penalty = 0
                if wire in inputs_of:
                    for cell in inputs_of[wire]:
                        if cell[0] == 'and':
                            a, b = cell[1], cell[2]
                            if a in covered_wires:
                                penalty += 0.5
                            if b in covered_wires:
                                penalty += 0.5
                        else:  # inv
                            a = cell[1]
                            if a in covered_wires:
                                penalty += 1.0

                adjusted_score = base_score - penalty * 5.0  # Penalty for overlap
                if adjusted_score > best_adjusted_score:
                    best_adjusted_score = adjusted_score
                    best_wire = wire

            if best_wire is None or best_adjusted_score <= 0:
                break  # No more good candidates

            w = best_wire
            if verbose:
                score = candidates[w]
                wire_depth = depth_map.get(w, 0)
                print(f"Chosen wire {w} (depth={wire_depth}, fanout={fanout_cache[w]}, "
                      f"score={score:.1f}, adjusted={best_adjusted_score:.1f})")

            cone = find_cone(netlist, k, w, fanout_cache, inputs_of, primary_inputs)

            if verbose:
                new_coverage = len(cone - covered_wires)
                print(f"  Cone: {cone} (new coverage: {new_coverage}/{len(cone)})")

            ins = netlist._create_or_lookup_wireset(cone)
            netlist.execute("INSERT OR IGNORE INTO luts (ins, out) VALUES (?, ?)", (ins, w))
            netlist.commit()

            # Mark all wires in cone as covered
            covered_wires.update(cone)
            covered_wires.add(w)
            luts_created += 1

        if verbose:
            print(f"Created {luts_created} LUTs (requested {cnt}, stopped early: {cnt - luts_created})")
            print(f"Total wire coverage: {len(covered_wires)} wires")

    else:
        # Original weighted random sampling approach
        wires = list(candidates.keys())
        weights = [candidates[w] for w in wires]
        chosen_wires = random.choices(wires, weights=weights, k=min(cnt, len(wires)))

        # Track covered wires to avoid redundant LUTs
        covered_outputs: set[int] = set()
        luts_created = 0

        for w in chosen_wires:
            # Skip if this output is already covered
            if w in covered_outputs:
                continue

            if verbose:
                score = candidates[w]
                wire_depth = depth_map.get(w, 0)
                print(f"Chosen wire {w} (depth={wire_depth}, fanout={fanout_cache[w]}, score={score:.1f})")

            cone = find_cone(netlist, k, w, fanout_cache, inputs_of, primary_inputs)

            if verbose:
                print(f"  Cone: {cone}")

            ins = netlist._create_or_lookup_wireset(cone)
            netlist.execute("INSERT OR IGNORE INTO luts (ins, out) VALUES (?, ?)", (ins, w))
            netlist.commit()

            # Mark this output as covered
            covered_outputs.add(w)
            luts_created += 1

        if verbose:
            print(f"Created {luts_created} LUTs (requested {cnt}, skipped {cnt - luts_created} duplicates)")
