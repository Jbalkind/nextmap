# Hello Nextmap

The introductory tutorial for nextmap, as an executable notebook
([`demo.ipynb`](demo.ipynb)). It walks through the core flow end to end:

1. **Retiming** — build a netlist from Yosys JSON, apply DFF-retiming rewrites,
   and extract a minimum-cost implementation via ILP (saves flip-flops).
2. **DSP technology mapping** — rewrite an arithmetic design and map
   multiply/multiply-add patterns onto Xilinx UltraScale+ DSP48E2 blocks, then
   compare against `synth_xilinx` in Yosys.

## Requirements

- `pip install -e ".[gurobi]"` from the repo root (CBC also works — set
  `SOLVER_TYPE = 'cbc'` in the notebook; no Gurobi license needed for that).
- `yosys` on your `PATH` (the notebook shells out to it for read/write_json and
  `stat`).
- Optionally build the native accelerator (`./nextmap/emapcc/build.sh`) for
  faster extraction — the notebook uses it automatically if present.

## Running

Run it from **this directory** so the `tests/*.v` inputs and the intermediate
JSON files resolve:

```bash
cd examples/hello-nextmap
jupyter nbconvert --to notebook --execute --inplace demo.ipynb
# or open it interactively: jupyter lab demo.ipynb
```

The `SCHEMA_PATH` is resolved from the installed `nextmap` package, so the
notebook is location-independent for the nextmap parts; only the Yosys `!`
shell-outs use repository-relative paths.
