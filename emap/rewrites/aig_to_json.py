"""
Convert AIG NetlistDB (with LUTs) to Yosys JSON format.

This allows us to export LUT-mapped designs back to JSON so they can be
combined with DSP-mapped cells and synthesized with Yosys.
"""

from typing import Dict, List, Any, Set, Tuple
import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from ..db_aig import NetlistDB as AIGNetlistDB


def compute_lut_truth_table(aig_db: AIGNetlistDB, input_wires: List[int], output_wire: int) -> int:
    """
    Compute the truth table for a LUT by evaluating the AIG logic.

    Args:
        aig_db: AIG database with AND and INV gates
        input_wires: List of input wire IDs (ordered)
        output_wire: Output wire ID

    Returns:
        INIT value as an integer (for k inputs, this is a 2^k bit number)
    """
    k = len(input_wires)
    if k > 6:
        # Xilinx LUTs max out at 6 inputs
        return 0

    # Build lookup tables for AND and INV gates
    and_gates = {}  # y -> (a, b)
    inv_gates = {}  # y -> a

    cur = aig_db.execute("SELECT a, b, y FROM ands")
    for a, b, y in cur.fetchall():
        and_gates[y] = (a, b)

    cur = aig_db.execute("SELECT a, y FROM invs")
    for a, y in cur.fetchall():
        inv_gates[y] = a

    # Evaluate the circuit for all input combinations
    truth_table = 0

    for input_val in range(2 ** k):
        # Set up input values
        wire_values = {}
        for i, wire in enumerate(input_wires):
            wire_values[wire] = (input_val >> i) & 1

        # Evaluate circuit (simple recursive evaluation with memoization)
        def eval_wire(wire):
            if wire in wire_values:
                return wire_values[wire]

            # Check if it's an AND gate output
            if wire in and_gates:
                a, b = and_gates[wire]
                wire_values[wire] = eval_wire(a) & eval_wire(b)
                return wire_values[wire]

            # Check if it's an INV gate output
            if wire in inv_gates:
                a = inv_gates[wire]
                wire_values[wire] = 1 - eval_wire(a)
                return wire_values[wire]

            # Constant wires (0, 1) or undefined (treat as 0)
            if wire == 0:
                return 0
            if wire == 1:
                return 1

            # Unknown wire - shouldn't happen, return 0
            return 0

        output_val = eval_wire(output_wire)
        if output_val:
            truth_table |= (1 << input_val)

    return truth_table


def aig_to_json_module(aig_db: AIGNetlistDB, wire_mapping: Dict[str, Any] = None,
                        module_name: str = "lut_mapped", start_fresh_wire_id: int = 200000,
                        skip_covered_gates: bool = True) -> Tuple[Dict[str, Any], Dict[int, int]]:
    """
    Convert AIG NetlistDB to Yosys JSON module format with fresh wire IDs.

    The AIG database contains:
    - ands (a, b, y): AND gates
    - invs (a, y): Inverters
    - luts (ins, out): LUTs with wireset of inputs

    We convert to Yosys cells with FRESH wire IDs to avoid conflicts:
    - Original wire IDs from bitblasting (inputs from original cells): kept as-is
    - Internal AIG wire IDs (100000+): renumbered to start_fresh_wire_id+
    - This avoids conflicts with DSP/DFF cells in merged design

    Args:
        aig_db: AIG NetlistDB with LUTs
        wire_mapping: Info about which original wires were bitblasted
        module_name: Name for the generated module
        start_fresh_wire_id: Starting ID for renumbered wires

    Returns:
        Tuple of (module dict, wire_renumber_map)
        - wire_renumber_map: old_wire_id -> new_wire_id
    """

    # Step 1: Collect all wire IDs from AIG database
    all_wires = set()
    original_wire_boundary = 10000  # Wires below this are from original design

    # Get LUTs
    cur = aig_db.execute("SELECT ins, out FROM luts")
    luts = cur.fetchall()

    # Get ANDs
    cur = aig_db.execute("SELECT a, b, y FROM ands")
    ands = cur.fetchall()

    # Get INVs
    cur = aig_db.execute("SELECT a, y FROM invs")
    invs = cur.fetchall()

    # Collect all wires
    for ins_wireset_id, out in luts:
        cur = aig_db.execute("SELECT wire_id FROM wireset_members WHERE wireset_id = ?", (ins_wireset_id,))
        input_wires = [w for (w,) in cur.fetchall()]
        all_wires.update(input_wires)
        all_wires.add(out)

    for a, b, y in ands:
        all_wires.update([a, b, y])

    for a, y in invs:
        all_wires.update([a, y])

    # IMPORTANT: Also include wires from bitblast_output_map
    # These are wires that DSP/DFF cells reference, even if they've been absorbed into LUTs
    if wire_mapping:
        bitblast_map = wire_mapping.get('bitblast_output_map', {})
        for old_wire, new_wires in bitblast_map.items():
            all_wires.update(new_wires)  # Add all bitblasted output wires

    # Step 2: Create wire renumbering map
    # - Wires < 10000: keep as-is (they're from original design, used as inputs)
    # - Wires >= 10000: renumber to fresh range (they're internal to bitblasted logic)
    wire_renumber = {}
    next_fresh_id = start_fresh_wire_id

    for wire_id in sorted(all_wires):
        if wire_id < original_wire_boundary:
            # Keep original wire IDs (these connect to DSP/DFF cells)
            wire_renumber[wire_id] = wire_id
        else:
            # Renumber internal wires to fresh range
            wire_renumber[wire_id] = next_fresh_id
            next_fresh_id += 1

    print(f"  Renumbered {len([w for w in all_wires if w >= original_wire_boundary])} internal wires to fresh IDs")

    # Helper function to renumber wire lists
    def renumber_wires(wire_list):
        return [wire_renumber.get(w, w) for w in wire_list]

    # Step 3: Build netlist dict with renumbered wires
    netlist = {
        "ports": {},
        "cells": {},
        "netnames": {}
    }

    # Create cells for LUTs
    skipped_identity_luts = 0
    for idx, (ins_wireset_id, out) in enumerate(luts):
        cur = aig_db.execute("SELECT wire_id FROM wireset_members WHERE wireset_id = ? ORDER BY wire_id",
                            (ins_wireset_id,))
        input_wires = [w for (w,) in cur.fetchall()]

        k = len(input_wires)

        # Skip identity LUTs (where input == output)
        # These are bugs from the LUT mapper and create combinational loops
        if k == 1 and input_wires[0] == out:
            skipped_identity_luts += 1
            continue

        # Compute truth table for this LUT
        init_value = compute_lut_truth_table(aig_db, input_wires, out)

        # Use renumbered wire IDs
        # Use Xilinx LUT primitives instead of generic $lut to prevent optimization
        # Map to LUT1-LUT6 based on number of inputs
        lut_types = {
            1: "LUT1",
            2: "LUT2",
            3: "LUT3",
            4: "LUT4",
            5: "LUT5",
            6: "LUT6"
        }

        cell_name = f"lut_{idx}"
        lut_type = lut_types.get(k, "LUT6")  # Default to LUT6 if k > 6

        # Create connections dict - LUT primitives use I0-I5 for inputs, O for output
        connections = {"O": renumber_wires([out])}
        for i, wire in enumerate(renumber_wires(input_wires[:6])):  # Max 6 inputs
            connections[f"I{i}"] = [wire]

        netlist["cells"][cell_name] = {
            "hide_name": 0,
            "type": lut_type,
            "parameters": {
                "INIT": init_value  # Actual truth table computed from AIG
            },
            "attributes": {
                "keep": 1  # Prevent Yosys from optimizing away
            },
            "port_directions": {
                **{f"I{i}": "input" for i in range(k)},
                "O": "output"
            },
            "connections": connections
        }

    if skipped_identity_luts > 0:
        print(f"  Skipped {skipped_identity_luts} identity LUTs (input==output)")

    # Collect wires covered by LUTs if skip_covered_gates is True
    covered_wires = set()
    if skip_covered_gates:
        for ins_wireset_id, out in luts:
            cur = aig_db.execute("SELECT wire_id FROM wireset_members WHERE wireset_id = ?", (ins_wireset_id,))
            input_wires = [w for (w,) in cur.fetchall()]

            # Skip identity LUTs from coverage
            if len(input_wires) == 1 and input_wires[0] == out:
                continue

            # Add all input wires to covered set
            covered_wires.update(input_wires)
            covered_wires.add(out)

        print(f"  Covered wires by LUTs: {len(covered_wires)}")

    # Create cells for AND gates (skip if covered by LUTs)
    skipped_ands = 0
    for idx, (a, b, y) in enumerate(ands):
        # Skip AND gates where output is covered by a LUT
        if skip_covered_gates and y in covered_wires:
            skipped_ands += 1
            continue

        cell_name = f"and_{idx}"
        netlist["cells"][cell_name] = {
            "hide_name": 0,
            "type": "$_AND_",
            "parameters": {},
            "attributes": {
                "keep": 1  # Prevent Yosys from optimizing away
            },
            "port_directions": {
                "A": "input",
                "B": "input",
                "Y": "output"
            },
            "connections": {
                "A": renumber_wires([a]),
                "B": renumber_wires([b]),
                "Y": renumber_wires([y])
            }
        }

    if skip_covered_gates and skipped_ands > 0:
        print(f"  Skipped {skipped_ands} AND gates covered by LUTs")

    # Create cells for INV gates (skip if covered by LUTs)
    skipped_invs = 0
    for idx, (a, y) in enumerate(invs):
        # Skip INV gates where output is covered by a LUT
        if skip_covered_gates and y in covered_wires:
            skipped_invs += 1
            continue

        cell_name = f"inv_{idx}"
        netlist["cells"][cell_name] = {
            "hide_name": 0,
            "type": "$_NOT_",
            "parameters": {},
            "attributes": {
                "keep": 1  # Prevent Yosys from optimizing away
            },
            "port_directions": {
                "A": "input",
                "Y": "output"
            },
            "connections": {
                "A": renumber_wires([a]),
                "Y": renumber_wires([y])
            }
        }

    if skip_covered_gates and skipped_invs > 0:
        print(f"  Skipped {skipped_invs} INV gates covered by LUTs")

    # Create netnames for all renumbered wires
    renumbered_wires = set(wire_renumber.values())
    for wire_id in renumbered_wires:
        netlist["netnames"][f"wire_{wire_id}"] = {
            "hide_name": 1,
            "bits": [wire_id],
            "attributes": {
                "keep": 1  # Prevent Yosys from optimizing away these wires
            }
        }

    return netlist, wire_renumber


def merge_aig_with_extracted_design(aig_module: Dict[str, Any],
                                      wire_renumber: Dict[int, int],
                                      extracted_mod: Dict[str, Any],
                                      wire_mapping: Dict[str, Any] = None,
                                      replaced_cell_types: Set[str] = None) -> Dict[str, Any]:
    """
    Replace bitblasted cells with LUT-mapped AIG logic.

    Strategy:
    1. Start with extracted design structure (ports, netnames)
    2. Remove cells of types that were bitblasted ($mux, $add, etc.)
    3. Add ALL cells from AIG module (LUTs, ANDs, INVs)
    4. Remap wires: old $mux output wires -> new bitblasted output wires
    5. Keep DSP and DFF cells unchanged

    This creates a clean design with:
    - DSP cells (from extraction)
    - DFF cells (from extraction)
    - LUT/AND/INV cells (from AIG, replacing the removed word-level cells)

    Args:
        aig_module: Module dict from aig_to_json_module()
        wire_renumber: Mapping from old AIG wire IDs to renumbered IDs
        extracted_mod: Extracted module from ILP (has DSPs, DFFs, word-level cells)
        wire_mapping: Wire mapping info from bitblasting (includes bitblast_output_map)
        replaced_cell_types: Set of cell types that were bitblasted (default: $mux)

    Returns:
        Combined module dict ready for Yosys
    """
    if replaced_cell_types is None:
        replaced_cell_types = {'$mux', '$add', '$sub'}

    if wire_mapping is None:
        wire_mapping = {}

    # Build wire remapping: old_wire_id -> new_wire_id (after renumbering)
    # This maps original $mux output wires to their bitblasted equivalents
    bitblast_output_map = wire_mapping.get('bitblast_output_map', {})

    # Create full wire remap: old -> new (renumbered)
    # For each old wire that was a bitblasted cell output, map it to the renumbered bitblasted output
    full_wire_remap = {}
    for old_wire, new_wires in bitblast_output_map.items():
        # new_wires is a list, but for simple cases it should be a single wire
        # We'll handle the case where it's a list of 1 element
        if len(new_wires) == 1:
            new_wire = new_wires[0]
            # Apply renumbering
            renumbered_wire = wire_renumber.get(new_wire, new_wire)
            full_wire_remap[old_wire] = renumbered_wire
        else:
            # Multiple wires - this shouldn't happen for bit-level mapping
            print(f"  Warning: old wire {old_wire} maps to multiple new wires: {new_wires}")

    print(f"  Wire remapping: {len(full_wire_remap)} wires to remap")

    # Helper function to remap a wire list
    def remap_wires(wire_list):
        return [full_wire_remap.get(w, w) if isinstance(w, int) else w for w in wire_list]

    # Start with a fresh design
    # Remap port bits as well
    remapped_ports = {}
    for port_name, port_info in extracted_mod.get("ports", {}).items():
        port_copy = dict(port_info)
        if 'bits' in port_copy:
            port_copy['bits'] = remap_wires(port_info['bits'])
        remapped_ports[port_name] = port_copy

    combined = {
        "ports": remapped_ports,
        "cells": {},
        "netnames": {}
    }

    # First pass: Add non-bitblasted cells from extracted design (DSPs, DFFs)
    # Apply wire remapping to all connections
    kept_cells = []
    removed_cells = []

    for cell_name, cell in extracted_mod.get("cells", {}).items():
        cell_type = cell.get("type", "")
        if cell_type not in replaced_cell_types:
            # Keep DSPs, DFFs, and tech-mapped cells
            # But remap their connections
            cell_copy = dict(cell)
            if 'connections' in cell_copy:
                cell_copy['connections'] = {
                    port: remap_wires(wires)
                    for port, wires in cell['connections'].items()
                }
            combined["cells"][cell_name] = cell_copy
            kept_cells.append(cell_type)
        else:
            removed_cells.append(cell_type)

    # Second pass: Add ALL cells from AIG module (these replace the removed cells)
    for cell_name, cell in aig_module.get("cells", {}).items():
        # No renaming needed - AIG cells have unique names (lut_X, and_X, inv_X)
        combined["cells"][cell_name] = cell

    # Merge netnames from both sources
    # Start with extracted design's netnames
    for net_name, net_info in extracted_mod.get("netnames", {}).items():
        combined["netnames"][net_name] = net_info

    # Add AIG netnames (for wires created during bitblasting)
    for net_name, net_info in aig_module.get("netnames", {}).items():
        # Only add if not already present
        if net_name not in combined["netnames"]:
            combined["netnames"][net_name] = net_info

    print(f"  Kept {len(kept_cells)} cells from extraction")
    print(f"  Removed {len(removed_cells)} bitblasted cells")
    print(f"  Added {len(aig_module.get('cells', {}))} AIG cells")

    return combined
