# nextmap

E-graph based technology mapping and optimization for hardware netlists.

`nextmap` ingests a [Yosys](https://github.com/YosysHQ/yosys) JSON netlist,
represents it in a relational e-graph (a SQL database of cells, wires, and
equivalence classes), applies rewrite rules, and extracts a minimum-cost
implementation via an ILP solver — including technology mapping onto DSP blocks
and LUTs.

## Features

- **Relational e-graph** over a SQL schema, with SQLite (default) or PostgreSQL
  database backends.
- **ILP extraction** with a pluggable solver interface: CBC (default, via PuLP)
  or Gurobi.
- **Rewrite library**: commutativity/associativity, DFF retiming (forward and
  backward), wide-operator splitting, arithmetic identities, and more
  (`nextmap/rewrites/`).
- **DSP technology mapping** onto Xilinx UltraScale+ DSP48E2 blocks.
- **LUT mapping** over AIGs, including bitblasting and AIG optimization
  (`nextmap/rewrites/lut.py`, `aig_opt.py`, `bitblast*.py`).
- **Yosys plugin** exposing a `nextmap` pass (`integrations/yosys/`).

## Installation

```bash
pip install -e .                 # core install (SQLite + CBC)
pip install -e ".[postgres]"     # add PostgreSQL backend
pip install -e ".[gurobi]"       # add Gurobi ILP backend (needs a license)
pip install -e ".[all]"          # all optional backends
pip install -e ".[examples]"     # Jupyter + matplotlib for the notebooks
```

Core dependencies (numpy, scipy, pulp) install automatically. CBC ships with
PuLP, so no proprietary solver is required for a working install.

### Optional native accelerator

`nextmap/emapcc/` contains a C++ implementation of the extraction hot paths
(`group_wires`, `prune_cells`). It is optional — nextmap falls back to pure
Python if it isn't built. To enable it (requires `cmake`, a C++17 compiler, and
`pip install pybind11`):

```bash
./nextmap/emapcc/build.sh
```

## Quick start

```python
import nextmap

# Build a netlist e-graph from a Yosys JSON module (in-memory SQLite by default)
netlist = nextmap.NetlistDB("nextmap/schema.sql")
netlist.build_from_json(module_json)
netlist.rebuild()

# Apply rewrites
matches = nextmap.rewrites.ematch_dff_forward_aby_cell(netlist, ["$mulu"])
nextmap.rewrites.apply_dff_forward_aby_cell(netlist, matches)
netlist.rebuild()

# Extract a minimum-cost implementation (solver_type: "auto" | "cbc" | "gurobi")
result = nextmap.extracts.ilp.extract_no_techmap(netlist, cost_model, solver_type="cbc")
```

### Database backend

`NetlistDB(schema_file, db_file_or_config=None, backend=None)` auto-selects the
backend: a path or `:memory:` → SQLite; a config dict or `"postgres"` →
PostgreSQL. Override with `backend="sqlite"|"postgres"` or the
`NEXTMAP_DB_BACKEND` environment variable. See
[`nextmap/README_SCHEMA.md`](nextmap/README_SCHEMA.md) and `DUAL_BACKEND_README.md`.

## Repository layout

```
nextmap/              the installable Python package
  db*.py              SQLite / PostgreSQL netlist e-graph backends
  schema*.sql         SQL schemas (shipped as package data)
  rewrites/           rewrite rules (arith, retiming, dsp, lut, aig, ...)
  extracts/           ILP extraction + solver interface (CBC / Gurobi)
integrations/yosys/   Yosys plugin exposing the `nextmap` pass
examples/             worked examples (e.g. timing-aware extraction)
eval/                 benchmark designs and evaluation scripts
```

## Yosys plugin

```bash
cd integrations/yosys && make
yosys -m ./nextmap_plugin_simple.so \
      -p "read_verilog design.v; prep; nextmap -strategy dsp; stat"
```

See [`integrations/yosys/README.md`](integrations/yosys/README.md).

## License

BSD 3-Clause. See [`LICENSE`](LICENSE).
