"""
In-place bitblasting: Bitblast word-level cells directly in the NetlistDB.

This approach avoids the complexity of maintaining separate databases and
wire ID remapping by bitblasting word-level operations directly into the
existing word-level NetlistDB. The bitblasted AND/INV gates coexist with
word-level cells in the same database.

Workflow:
1. Load design into word-level NetlistDB (from Yosys JSON)
2. Identify cells to bitblast (exclude DSPs, DFFs)
3. For each word-level cell, create equivalent AND/INV gates
4. Track wire mappings (word-level signal -> bit-level wires)
5. Run equality saturation on bit-level gates
6. LUT map the optimized gates
7. Export everything together
"""

from typing import Dict, List, Set, Tuple, Any
from ..db import NetlistDB
from . import bitblast


class InPlaceBitblaster:
    """Manages in-place bitblasting within a NetlistDB."""

    def __init__(self, netlist: NetlistDB, start_wire_id: int = 1000000):
        """
        Initialize in-place bitblaster.

        Args:
            netlist: The word-level NetlistDB to bitblast into
            start_wire_id: Starting wire ID for bitblasted wires (use high value to avoid conflicts)
        """
        self.netlist = netlist
        self.ctx = bitblast.BitblastContext(start_wire_id)

        # Track which word-level wires have been bitblasted
        # Maps: word_level_wirevec_id -> list of bit-level wire IDs
        self.bitblasted_signals: Dict[int, List[int]] = {}

        # Track which cells have been bitblasted (to avoid duplication)
        self.bitblasted_cells: Set[str] = set()

    def bitblast_cell(self, cell_name: str, cell_type: str,
                     connections: Dict[str, int]) -> int:
        """
        Bitblast a single word-level cell and insert AND/INV gates into the database.

        Args:
            cell_name: Name of the cell
            cell_type: Type of cell ($add, $mul, etc.)
            connections: Dict mapping port names to wirevec IDs

        Returns:
            Wirevec ID for the output signal
        """
        if cell_name in self.bitblasted_cells:
            raise ValueError(f"Cell {cell_name} already bitblasted")

        # Convert wirevec IDs to bit vectors
        connections_bits: Dict[str, List[int]] = {}
        for port, wirevec_id in connections.items():
            bits = self.netlist._get_wirevec(wirevec_id)
            connections_bits[port] = bits

        # Bitblast this cell
        output_bits = bitblast.bitblast_cell(self.ctx, cell_type, connections_bits)

        # Insert the generated AND/INV gates into the database
        for a, b, y in self.ctx.and_gates:
            self.netlist.execute("INSERT OR IGNORE INTO ands (a, b, y) VALUES (?, ?, ?)",
                               (a, b, y))

        for a, y in self.ctx.inv_gates:
            self.netlist.execute("INSERT OR IGNORE INTO invs (a, y) VALUES (?, ?)",
                              (a, y))

        self.netlist.commit()

        # Create a wirevec for the output
        output_wirevec_id = self.netlist._create_or_lookup_wirevec(output_bits)

        # Track this cell as bitblasted
        self.bitblasted_cells.add(cell_name)

        # Store the mapping
        if 'Y' in connections:
            self.bitblasted_signals[connections['Y']] = output_bits

        return output_wirevec_id

    def get_bitblast_stats(self) -> Dict[str, int]:
        """Get statistics about bitblasted design."""
        cur = self.netlist.execute("SELECT COUNT(*) FROM ands")
        and_count = cur.fetchone()[0]

        cur = self.netlist.execute("SELECT COUNT(*) FROM invs")
        inv_count = cur.fetchone()[0]

        return {
            'and_gates': and_count,
            'inv_gates': inv_count,
            'total_gates': and_count + inv_count,
            'cells_bitblasted': len(self.bitblasted_cells),
            'signals_mapped': len(self.bitblasted_signals)
        }


def bitblast_extracted_design_inplace(extracted_mod: Dict[str, Any],
                                      schema_file: str = "emap/schema.sql") -> Tuple[NetlistDB, InPlaceBitblaster, Dict]:
    """
    Bitblast an extracted design (from ILP) in place.

    This directly bitblasts the word-level cells from the extraction output,
    keeping DSPs and DFFs intact in the same database.

    Args:
        extracted_mod: Extracted module dict (from ILP extraction)
        schema_file: Path to database schema

    Returns:
        Tuple of (NetlistDB with bitblasted gates, InPlaceBitblaster, wire_mapping)
    """
    # Create NetlistDB
    netlist = NetlistDB(schema_file=schema_file, db_file=":memory:", cnt=100000)

    # Create bitblaster
    bitblaster = InPlaceBitblaster(netlist, start_wire_id=1000000)

    cells = extracted_mod.get('cells', {})
    ports = extracted_mod.get('ports', {})

    print(f"🔨 Bitblasting extracted design in place:")
    print(f"  Total cells: {len(cells)}")

    # Track wire mappings: original_wire_id -> bitblasted_wire_ids
    wire_mapping = {}

    # Classify cells
    dsp_cells = []
    dff_cells = []
    word_cells = []

    for cell_name, cell_info in cells.items():
        cell_type = cell_info.get('type')

        if cell_type == '$dff':
            dff_cells.append((cell_name, cell_info))
        elif 'mul' in cell_type or 'dsp' in cell_type.lower():
            dsp_cells.append((cell_name, cell_info))
        else:
            word_cells.append((cell_name, cell_info))

    print(f"  DSP cells: {len(dsp_cells)}")
    print(f"  DFF cells: {len(dff_cells)}")
    print(f"  Word-level cells to bitblast: {len(word_cells)}")

    # Bitblast word-level cells
    for cell_name, cell_info in word_cells:
        cell_type = cell_info.get('type')
        connections = cell_info.get('connections', {})

        # Extract bit-level connections
        connections_bits = {}
        for port, bits in connections.items():
            # bits is already a list of wire IDs from the extracted design
            connections_bits[port] = bits

        try:
            # Bitblast this cell
            output_bits = bitblast.bitblast_cell(bitblaster.ctx, cell_type, connections_bits)

            # Track mapping for output wires
            if 'Y' in connections:
                for i, orig_wire in enumerate(connections['Y']):
                    if i < len(output_bits):
                        if orig_wire not in wire_mapping:
                            wire_mapping[orig_wire] = []
                        wire_mapping[orig_wire].append(output_bits[i])

            bitblaster.bitblasted_cells.add(cell_name)

        except Exception as e:
            print(f"  ⚠️  Warning: Failed to bitblast {cell_name} ({cell_type}): {e}")
            continue

    # Insert all AND/INV gates into the database
    print(f"\n📝 Inserting bitblasted gates into database...")
    for a, b, y in bitblaster.ctx.and_gates:
        netlist.execute("INSERT OR IGNORE INTO ands (a, b, y) VALUES (?, ?, ?)", (a, b, y))

    for a, y in bitblaster.ctx.inv_gates:
        netlist.execute("INSERT OR IGNORE INTO invs (a, y) VALUES (?, ?)", (a, y))

    # Compute which wires are used by bitblasted gates
    all_wires_used_by_gates = set()
    for a, b, y in bitblaster.ctx.and_gates:
        all_wires_used_by_gates.update([a, b])
    for a, y in bitblaster.ctx.inv_gates:
        all_wires_used_by_gates.add(a)

    # Compute which wires are produced by bitblasted gates
    all_wires_produced_by_gates = set()
    for a, b, y in bitblaster.ctx.and_gates:
        all_wires_produced_by_gates.add(y)
    for a, y in bitblaster.ctx.inv_gates:
        all_wires_produced_by_gates.add(y)

    # Primary inputs = wires used but not produced
    bitblasted_primary_inputs = all_wires_used_by_gates - all_wires_produced_by_gates

    print(f"\n🔍 Identifying primary inputs for bitblasted logic...")
    print(f"  Wires used by gates: {len(all_wires_used_by_gates)}")
    print(f"  Wires produced by gates: {len(all_wires_produced_by_gates)}")
    print(f"  Primary inputs (used - produced): {len(bitblasted_primary_inputs)}")

    # Mark these as from_inputs
    for wire in bitblasted_primary_inputs:
        netlist.execute("INSERT OR IGNORE INTO from_inputs (source) VALUES (?)", (wire,))

    # Also mark module primary inputs
    for port_name, port_info in ports.items():
        if port_info['direction'] == 'input':
            bits = port_info['bits']
            for bit in bits:
                netlist.execute("INSERT OR IGNORE INTO from_inputs (source) VALUES (?)", (bit,))

    # Mark outputs from DSP cells as circuit outputs (they need to be preserved)
    print(f"\n🎯 Marking DSP outputs as circuit outputs...")
    dsp_output_count = 0
    for cell_name, cell_info in dsp_cells:
        connections = cell_info.get('connections', {})
        for port, bits in connections.items():
            # Common DSP output ports
            if port in ['Y', 'P', 'PCOUT', 'c_out']:
                for bit in bits:
                    # Check if this bit is used by bitblasted logic (is it a primary input?)
                    if bit in bitblasted_primary_inputs:
                        netlist.execute("INSERT OR IGNORE INTO as_outputs (sink, name) VALUES (?, ?)",
                                      (bit, f"{cell_name}_{port}_{bit}"))
                        dsp_output_count += 1

    print(f"  Marked {dsp_output_count} DSP output wires")

    netlist.commit()

    stats = bitblaster.get_bitblast_stats()
    print(f"\n✅ Bitblasting complete:")
    print(f"  AND gates: {stats['and_gates']}")
    print(f"  INV gates: {stats['inv_gates']}")
    print(f"  Total gates: {stats['total_gates']}")
    print(f"  Cells bitblasted: {stats['cells_bitblasted']}")

    return netlist, bitblaster, wire_mapping


def optimize_and_map_luts(netlist: NetlistDB,
                          bitblaster: InPlaceBitblaster,
                          k: int = 6,
                          lut_count: int = 100,
                          rseed: int = 42) -> Dict[str, Any]:
    """
    Optimize bitblasted gates and map to LUTs.

    Args:
        netlist: NetlistDB with bitblasted gates
        bitblaster: InPlaceBitblaster instance
        k: LUT input size
        lut_count: Number of LUTs to create
        rseed: Random seed

    Returns:
        Statistics about the mapping
    """
    from . import aig_opt
    from .lut import techmap_luts

    # Run equality saturation on the bit-level gates
    print("\n🔧 Optimizing bitblasted gates with equality saturation...")
    aig_opt.optimize_aig_with_eqsat(
        netlist,
        max_iterations=10,
        enable_associativity=False,
        verbose=True
    )

    # Get stats after optimization
    stats_before_lut = bitblaster.get_bitblast_stats()
    print(f"\n📊 After equality saturation:")
    print(f"  AND gates: {stats_before_lut['and_gates']}")
    print(f"  INV gates: {stats_before_lut['inv_gates']}")

    # AIG balancing disabled - it wasn't improving results and has recursion issues
    # balance_stats = aig_opt.balance_aig(netlist, verbose=True)

    # Map to LUTs
    print(f"\n🗺️  Mapping to {k}-LUTs...")
    techmap_luts(netlist, k=k, cnt=lut_count, rseed=rseed, greedy=True, verbose=True)

    # Get LUT stats
    cur = netlist.execute("SELECT COUNT(*) FROM luts")
    lut_count_actual = cur.fetchone()[0]

    stats = {
        **stats_before_lut,
        'luts_created': lut_count_actual
    }

    print(f"\n✅ Mapping complete:")
    print(f"  LUTs created: {lut_count_actual}")

    return stats
