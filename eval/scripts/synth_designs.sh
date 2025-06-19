#!/bin/bash
# Synthesize the benchmark designs the eval scripts consume, into eval/out/.
#
# Produces, for each design:
#   - word-level JSON  (eval/out/<name>.json)        : $mul/$add/$dff/$mux, module "top".
#       Used by the DSP / extraction / backend-matrix tests (sqlite/postgres x cbc/gurobi).
#   - combinational AIG (eval/out/<name>_aig.json)   : 1-bit $and/$not, registers cut.
#       Used by the LUT-mapping tests (the db_aig backend is combinational-only).
#
# Run from the repository root. Requires `yosys` on PATH.
set -e
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
mkdir -p eval/out

SYSTOLIC="systolic_matmul_4x4_w8 systolic_matmul_4x4_w16 systolic_matmul_4x4_w32 \
          systolic_matmul_8x8_w8 systolic_matmul_8x8_w16 systolic_matmul_8x8_w32"
FIR="fir_n16_w8 fir_n16_w16 fir_n16_w32 fir_n32_w8"

echo "== word-level (DSP flow) =="
for f in $SYSTOLIC; do
    yosys -q -p "read_verilog eval/systolic/$f.v; proc; memory_map; opt; write_json eval/out/$f.json"
    echo "  eval/out/$f.json"
done
for f in $FIR; do
    # dffunmap lowers $sdff (sync-reset FFs) to $dff + $mux, which the word-level
    # NetlistDB supports; a trailing full `opt` would re-infer $sdff, so don't.
    yosys -q -p "read_verilog eval/fir/$f.v; proc; opt; dffunmap; opt_clean; write_json eval/out/$f.json"
    echo "  eval/out/$f.json"
done

# Only the smaller FIRs are bit-blasted to AIGs: wider multipliers explode into
# enormous gate-level netlists (tens of MB+) and are not needed by the LUT tests.
FIR_AIG="fir_n16_w8 fir_n16_w16"
echo "== combinational AIG (LUT flow): registers cut, 1-bit \$and/\$not ports =="
for f in $FIR_AIG; do
    yosys -q -p "
        read_verilog eval/fir/$f.v
        proc; flatten; opt; techmap; opt
        abc -g AND
        delete t:\$_DFF_P_ t:\$_SDFF_PP0_
        opt_clean -purge
        splitnets -ports
        write_verilog /tmp/${f}_comb.v
    "
    yosys -q -p "read_verilog /tmp/${f}_comb.v; proc; opt_clean; splitnets -ports; write_json eval/out/${f}_aig.json"
    echo "  eval/out/${f}_aig.json"
done

# EPFL combinational benchmarks: the checked-in eval/epfl/*.v are ABC-written
# AIGs; lower them to the 1-bit $and/$not word-level JSON the db_aig backend
# wants. Written back into eval/epfl/ (gitignored) since that's where the
# notebook and LUT eval scripts look for them.
echo "== EPFL combinational AIG (LUT flow): 1-bit \$and/\$not ports =="
for f in adder; do
    yosys -q -p "
        read_verilog eval/epfl/$f.v
        flatten; opt; techmap; opt
        abc -g AND
        opt_clean -purge
        splitnets -ports
        write_verilog /tmp/epfl_${f}_comb.v
    "
    yosys -q -p "read_verilog /tmp/epfl_${f}_comb.v; proc; opt_clean; splitnets -ports; write_json eval/epfl/${f}.json"
    echo "  eval/epfl/${f}.json"
done

echo "Done."
