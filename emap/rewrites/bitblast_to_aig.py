"""
Convert bitblasted designs to AIG NetlistDB for LUT mapping.

This module takes the output of bitblasting (AND/INV gates) and creates
an AIG NetlistDB that can be used with the LUT mapper.
"""

from typing import List, Tuple, Dict, Any
import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from ..db_aig import NetlistDB as AIGNetlistDB
from . import bitblast


def create_aig_from_bitblasted(and_gates: List[Tuple[int, int, int]],
                                inv_gates: List[Tuple[int, int]],
                                ports: Dict[str, Dict[str, Any]],
                                schema_file: str = "emap/schema.sql") -> AIGNetlistDB:
    """
    Create an AIG NetlistDB from bitblasted gates.

    Args:
        and_gates: List of (a, b, y) AND gate tuples
        inv_gates: List of (a, y) INV gate tuples
        ports: Port definitions from original design
        schema_file: Path to database schema

    Returns:
        AIGNetlistDB ready for LUT mapping
    """
    # Create AIG database
    netlist = AIGNetlistDB(schema_file=schema_file, db_file=":memory:", cnt=100000)

    # Add all AND gates
    for a, b, y in and_gates:
        netlist.execute("INSERT OR IGNORE INTO ands (a, b, y) VALUES (?, ?, ?)", (a, b, y))

    # Add all INV gates
    for a, y in inv_gates:
        netlist.execute("INSERT OR IGNORE INTO invs (a, y) VALUES (?, ?)", (a, y))

    # TODO: Map ports correctly
    # For now, skip port mapping as we're focused on internal logic

    netlist.commit()

    print(f"Created AIG NetlistDB:")
    print(f"  AND gates: {len(and_gates)}")
    print(f"  INV gates: {len(inv_gates)}")

    return netlist


def bitblast_extracted_design(extracted_mod: Dict[str, Any],
                               schema_file: str = "emap/schema.sql") -> AIGNetlistDB:
    """
    Bitblast an extracted design (from ILP) and create AIG NetlistDB.

    This is the main entry point for the DSP+LUT workflow:
    1. Takes extracted design from ILP (word-level cells + DSPs)
    2. Bitblasts word-level cells to AND/INV
    3. Creates AIG NetlistDB for LUT mapping

    Args:
        extracted_mod: Extracted module dict (from ILP extraction)
        schema_file: Path to schema file

    Returns:
        AIG NetlistDB ready for LUT mapping
    """
    cells = extracted_mod.get('cells', {})
    ports = extracted_mod.get('ports', {})

    print(f"Bitblasting extracted design:")
    print(f"  Total cells: {len(cells)}")

    # Separate cells by type
    bitblast_cells = {}
    skip_cells = {}

    for name, cell in cells.items():
        cell_type = cell.get('type')
        if cell_type in ['$dff', 'dsp_generic']:
            skip_cells[name] = cell
        else:
            bitblast_cells[name] = cell

    print(f"  Cells to bitblast: {len(bitblast_cells)}")
    print(f"  Cells to skip (DFF/DSP): {len(skip_cells)}")

    # Bitblast the word-level cells
    and_gates, inv_gates, next_wire_id = bitblast.bitblast_design(
        bitblast_cells,
        start_wire_id=100000  # Use high wire IDs to avoid conflicts
    )

    print(f"  Generated {len(and_gates)} AND gates, {len(inv_gates)} INV gates")

    # Create AIG NetlistDB
    aig_db = create_aig_from_bitblasted(and_gates, inv_gates, ports, schema_file)

    return aig_db


def analyze_bitblasted_design(aig_db: AIGNetlistDB):
    """Print statistics about a bitblasted design."""
    cur = aig_db.execute("SELECT COUNT(*) FROM ands")
    and_count = cur.fetchone()[0]

    cur = aig_db.execute("SELECT COUNT(*) FROM invs")
    inv_count = cur.fetchone()[0]

    print(f"\n📊 Bitblasted Design Statistics:")
    print(f"  AND gates: {and_count}")
    print(f"  INV gates: {inv_count}")
    print(f"  Total gates: {and_count + inv_count}")

    # Estimate LUT mapping potential
    # Assume k=6 LUTs, each can cover ~5 gates on average
    estimated_luts = (and_count + inv_count) // 5
    print(f"  Estimated LUTs needed (k=6): ~{estimated_luts}")

    return {
        'and_gates': and_count,
        'inv_gates': inv_count,
        'total_gates': and_count + inv_count,
        'estimated_luts': estimated_luts
    }
