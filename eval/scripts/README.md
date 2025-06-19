# Evaluation & benchmark scripts

Ad-hoc scripts used during development to evaluate and benchmark nextmap
(DSP/LUT mapping comparisons, 4-way backend matrices, SQLite/PostgreSQL and
CBC/Gurobi benchmarks, ABC/Yosys driver scripts).

These are not part of the installable `nextmap` package and are not a formal
test suite. Many reference repository-relative paths (e.g. design files under
`eval/`, `nextmap/schema.sql`), so run them from the **repository root**.

## Generating inputs first

Most scripts read pre-synthesized netlists from `eval/out/` (gitignored).
Generate them all from the checked-in source designs with:

```bash
./eval/scripts/synth_designs.sh      # needs yosys on PATH
```

This writes, per design, a word-level JSON (for the DSP / extraction / backend
tests) and — for the small FIRs — a combinational `$and`/`$not` AIG `*_aig.json`
(for the LUT tests; the `db_aig` backend is combinational-only). It also lowers
the checked-in EPFL `eval/epfl/*.v` benchmarks to `eval/epfl/adder.json` (the
AIG JSON the LUT notebook and `compare_lut_mapping.py` / `analyze_lut_quality.py`
/ `test.py` consume). All generated JSON is gitignored, so run this once after a
fresh checkout.

## What's verified

With Postgres running, Gurobi licensed, and yosys on PATH (run from the repo
root after `synth_designs.sh`), these pass:

- `test_4way_matrix.py`, `test_4way_matrix_with_resources.py` — 4/4 of
  {sqlite,postgres} x {cbc,gurobi} on systolic 4x4_w8 (DSP mapping).
- `test_sqlite_cbc_8x8.py`, `run_eval_systolic_8x8_test.py` — 8x8 across all
  backends (CBC now solves the 8x8 case; both solvers agree).
- `test_dsp_lut_integration.py`, `test_dsp_plus_lut_mapping.py`,
  `test_dsp_vs_dsp_lut_with_yosys.py`, `analyze_unmapped_after_dsp.py` —
  DSP extraction -> bitblast -> LUT pipeline (and a synth_xilinx comparison).
- `compare_lut_mapping.py`, `compare_lut_fir.py`, `analyze_lut_quality.py`,
  `test.py` — AIG eq-sat + LUT mapping (`db_aig` + `aig_opt` + `techmap_luts`).
- `benchmark_postgresql_performance.py` — per-phase timing on the PostgreSQL
  backend across the FIR designs.

Caveats:
- `comprehensive_4way_evaluation.py` and `test_4way_matrix_8x8_w32.py` exercise
  large 8x8 (w32) ILP solves; individual configs succeed but the full sweep is
  slow (minutes), so a wall-clock cap will cut them off mid-run.
- `setup_postgres.py` provisions a local PostgreSQL database; the backend
  defaults to a `nextmap_temp` database over the Unix socket as the current user.
- `test_postgres.py` is a stale basic-connection check (wrong class + schema
  path); superseded by the backend coverage in `test_4way_matrix.py` /
  `test_dual_backend_systolic.py`.
