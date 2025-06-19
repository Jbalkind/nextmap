"""
Bitblasting: Convert word-level operations to bit-level AIG (AND/INV gates).

This module converts word-level cells ($add, $mul, $mux, etc.) into
equivalent networks of single-bit AND and INV gates for LUT mapping.
"""

from typing import List, Tuple, Dict, Any


class BitblastContext:
    """Context for bitblasting that tracks wire IDs and creates gates."""

    def __init__(self, start_wire_id: int = 10000):
        self.next_wire_id = start_wire_id
        self.and_gates: List[Tuple[int, int, int]] = []  # (a, b, y)
        self.inv_gates: List[Tuple[int, int]] = []  # (a, y)

    def fresh_wire(self) -> int:
        """Allocate a fresh wire ID."""
        wire_id = self.next_wire_id
        self.next_wire_id += 1
        return wire_id

    def add_and(self, a: int, b: int) -> int:
        """Create an AND gate and return its output wire."""
        y = self.fresh_wire()
        self.and_gates.append((a, b, y))
        return y

    def add_inv(self, a: int) -> int:
        """Create an INV gate and return its output wire."""
        y = self.fresh_wire()
        self.inv_gates.append((a, y))
        return y

    def add_or(self, a: int, b: int) -> int:
        """Create OR gate: a | b = ~(~a & ~b)."""
        not_a = self.add_inv(a)
        not_b = self.add_inv(b)
        nand = self.add_and(not_a, not_b)
        return self.add_inv(nand)

    def add_xor(self, a: int, b: int) -> int:
        """Create XOR gate: a ^ b = (a & ~b) | (~a & b)."""
        not_a = self.add_inv(a)
        not_b = self.add_inv(b)
        left = self.add_and(a, not_b)
        right = self.add_and(not_a, b)
        return self.add_or(left, right)


def bitblast_add(ctx: BitblastContext, a_vec: List[int], b_vec: List[int],
                 signed: bool = False) -> List[int]:
    """
    Bitblast an addition operation to ripple-carry adder.

    Args:
        ctx: Bitblasting context
        a_vec: Input A bits (LSB first)
        b_vec: Input B bits (LSB first)
        signed: Whether this is signed addition

    Returns:
        List of output bits (LSB first)
    """
    width = len(a_vec)
    assert len(b_vec) == width, "Operands must have same width"

    result = []
    carry = 0  # Constant 0 wire

    for i in range(width):
        # Full adder logic:
        # sum[i] = a[i] ^ b[i] ^ carry
        # carry_out = (a[i] & b[i]) | (a[i] & carry) | (b[i] & carry)

        # Sum bit
        xor_ab = ctx.add_xor(a_vec[i], b_vec[i])
        sum_bit = ctx.add_xor(xor_ab, carry) if carry != 0 else xor_ab
        result.append(sum_bit)

        # Carry out
        if i < width - 1 or not signed:  # Need carry for next bit
            ab = ctx.add_and(a_vec[i], b_vec[i])
            if carry != 0:
                ac = ctx.add_and(a_vec[i], carry)
                bc = ctx.add_and(b_vec[i], carry)
                ab_or_ac = ctx.add_or(ab, ac)
                carry = ctx.add_or(ab_or_ac, bc)
            else:
                carry = ab

    return result


def bitblast_sub(ctx: BitblastContext, a_vec: List[int], b_vec: List[int],
                 signed: bool = False) -> List[int]:
    """
    Bitblast subtraction: a - b = a + (~b + 1).

    Uses two's complement: negate b and add.
    """
    width = len(b_vec)

    # Invert all bits of b
    b_inverted = [ctx.add_inv(b) for b in b_vec]

    # Add 1: set initial carry to 1 (need constant-1 wire)
    # For now, create 1 by inverting 0
    one = ctx.add_inv(0)

    result = []
    carry = one

    for i in range(width):
        # Full adder with inverted b
        xor_ab = ctx.add_xor(a_vec[i], b_inverted[i])
        sum_bit = ctx.add_xor(xor_ab, carry)
        result.append(sum_bit)

        # Carry
        if i < width - 1:
            ab = ctx.add_and(a_vec[i], b_inverted[i])
            ac = ctx.add_and(a_vec[i], carry)
            bc = ctx.add_and(b_inverted[i], carry)
            ab_or_ac = ctx.add_or(ab, ac)
            carry = ctx.add_or(ab_or_ac, bc)

    return result


def bitblast_mul(ctx: BitblastContext, a_vec: List[int], b_vec: List[int],
                 signed: bool = False) -> List[int]:
    """
    Bitblast multiplication using shift-and-add algorithm.

    For n-bit x m-bit multiplication:
    - Creates partial products (AND gates)
    - Sums them with shifted positions

    Output width = len(a_vec) + len(b_vec) for full precision,
    but we truncate to len(a_vec) to match typical behavior.
    """
    width_a = len(a_vec)
    width_b = len(b_vec)
    output_width = width_a  # Truncate to input width

    # Partial products: pp[i][j] = a[i] & b[j]
    partial_products = []
    for i in range(width_b):
        pp_row = []
        for j in range(width_a):
            pp = ctx.add_and(a_vec[j], b_vec[i])
            pp_row.append(pp)
        partial_products.append(pp_row)

    # Sum partial products with appropriate shifts
    # Start with first partial product
    accumulator = partial_products[0][:output_width]

    for i in range(1, width_b):
        # Shift partial product i by i positions
        shifted_pp = [0] * i + partial_products[i][:output_width - i]

        # Pad to output width
        while len(shifted_pp) < output_width:
            shifted_pp.append(0)
        shifted_pp = shifted_pp[:output_width]

        # Add to accumulator
        accumulator = bitblast_add(ctx, accumulator, shifted_pp, signed=False)

    return accumulator


def bitblast_mux(ctx: BitblastContext, a_vec: List[int], b_vec: List[int],
                 sel: int) -> List[int]:
    """
    Bitblast multiplexer: out = sel ? b : a.

    For each bit i: out[i] = (sel & b[i]) | (~sel & a[i])
    """
    width = len(a_vec)
    assert len(b_vec) == width, "Mux inputs must have same width"

    result = []
    not_sel = ctx.add_inv(sel)

    for i in range(width):
        sel_and_b = ctx.add_and(sel, b_vec[i])
        notsel_and_a = ctx.add_and(not_sel, a_vec[i])
        out_bit = ctx.add_or(sel_and_b, notsel_and_a)
        result.append(out_bit)

    return result


def bitblast_logic_and(ctx: BitblastContext, a_vec: List[int], b_vec: List[int]) -> List[int]:
    """Bitblast bitwise AND: out[i] = a[i] & b[i]."""
    width = len(a_vec)
    return [ctx.add_and(a_vec[i], b_vec[i]) for i in range(width)]


def bitblast_logic_or(ctx: BitblastContext, a_vec: List[int], b_vec: List[int]) -> List[int]:
    """Bitblast bitwise OR: out[i] = a[i] | b[i]."""
    width = len(a_vec)
    return [ctx.add_or(a_vec[i], b_vec[i]) for i in range(width)]


def bitblast_logic_xor(ctx: BitblastContext, a_vec: List[int], b_vec: List[int]) -> List[int]:
    """Bitblast bitwise XOR: out[i] = a[i] ^ b[i]."""
    width = len(a_vec)
    return [ctx.add_xor(a_vec[i], b_vec[i]) for i in range(width)]


def bitblast_not(ctx: BitblastContext, a_vec: List[int]) -> List[int]:
    """Bitblast bitwise NOT: out[i] = ~a[i]."""
    return [ctx.add_inv(a) for a in a_vec]


def bitblast_cell(ctx: BitblastContext, cell_type: str,
                  connections: Dict[str, List[int]]) -> List[int]:
    """
    Bitblast a single word-level cell.

    Args:
        ctx: Bitblasting context
        cell_type: Type of cell ($adds, $mulu, $mux, etc.)
        connections: Dict mapping port names to wire ID lists

    Returns:
        List of output wire IDs
    """
    if cell_type in ['$adds', '$addu', '$add']:
        a = connections['A']
        b = connections['B']
        signed = cell_type == '$adds'
        return bitblast_add(ctx, a, b, signed)

    elif cell_type in ['$subs', '$subu', '$sub']:
        a = connections['A']
        b = connections['B']
        signed = cell_type == '$subs'
        return bitblast_sub(ctx, a, b, signed)

    elif cell_type in ['$muls', '$mulu', '$mul']:
        a = connections['A']
        b = connections['B']
        signed = cell_type == '$muls'
        return bitblast_mul(ctx, a, b, signed)

    elif cell_type == '$mux':
        a = connections['A']
        b = connections['B']
        s = connections['S']
        assert len(s) == 1, "Mux select must be 1-bit"
        return bitblast_mux(ctx, a, b, s[0])

    elif cell_type in ['$ands', '$andu']:
        return bitblast_logic_and(ctx, connections['A'], connections['B'])

    elif cell_type in ['$ors', '$oru']:
        return bitblast_logic_or(ctx, connections['A'], connections['B'])

    elif cell_type in ['$xors', '$xoru']:
        return bitblast_logic_xor(ctx, connections['A'], connections['B'])

    elif cell_type == '$not':
        return bitblast_not(ctx, connections['A'])

    else:
        raise ValueError(f"Unsupported cell type for bitblasting: {cell_type}")


def bitblast_design(cells: Dict[str, Dict[str, Any]],
                    start_wire_id: int = 10000) -> Tuple[List[Tuple], List[Tuple], int]:
    """
    Bitblast an entire design (collection of word-level cells).

    Args:
        cells: Dict of cell_name -> cell_info (type, connections, etc.)
        start_wire_id: Starting wire ID for fresh wires

    Returns:
        Tuple of (and_gates, inv_gates, next_wire_id)
        - and_gates: List of (a, b, y) tuples
        - inv_gates: List of (a, y) tuples
        - next_wire_id: Next available wire ID
    """
    ctx = BitblastContext(start_wire_id)

    # Track output mappings: old_wire_id -> new_wire_list
    wire_mappings: Dict[int, List[int]] = {}

    for cell_name, cell in cells.items():
        cell_type = cell.get('type')

        # Skip cells that don't need bitblasting
        if cell_type in ['$dff', 'dsp_generic']:
            continue

        connections = cell.get('connections', {})

        try:
            # Bitblast this cell
            output_bits = bitblast_cell(ctx, cell_type, connections)

            # Store mapping for output wires
            if 'Y' in connections:
                for i, old_wire in enumerate(connections['Y']):
                    if i < len(output_bits):
                        if old_wire not in wire_mappings:
                            wire_mappings[old_wire] = []
                        wire_mappings[old_wire].append(output_bits[i])

        except ValueError as e:
            print(f"Warning: Skipping cell {cell_name}: {e}")
            continue

    return ctx.and_gates, ctx.inv_gates, ctx.next_wire_id
