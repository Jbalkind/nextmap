# Nextmap Yosys Plugin

A simple [Yosys](https://github.com/YosysHQ/yosys) plugin that exposes nextmap's
rewrite/extraction passes as a `nextmap` command inside Yosys. The plugin is a
thin C++ shim: it writes the current design to JSON, hands it to
`nextmap_runner.py` (which drives the `nextmap` Python package), and reads the
optimized design back.

## Requirements

- A Yosys install providing `yosys-config` on your `PATH`.
- The `nextmap` package importable by the `python3` that the plugin shells out to
  (e.g. `pip install -e .` from the repo root).

## Build

```bash
cd integrations/yosys
make            # produces nextmap_plugin_simple.so
make test       # sanity check: prints `help nextmap`
make install    # optional: copy the .so into Yosys' plugin dir
```

## Usage

```
yosys -m ./nextmap_plugin_simple.so \
      -p "read_verilog design.v; prep; nextmap -strategy dsp; stat"
```

### `nextmap` options

| Option | Default | Meaning |
| --- | --- | --- |
| `-strategy <type>` | `basic` | `basic`, `retiming`, `comprehensive`, or `dsp` |
| `-iterations <n>` | `10` | max rewrite iterations |
| `-schema <path>` | packaged schema | nextmap SQL schema |
| `-runner <path>` | `./nextmap_runner.py` | path to the Python driver |
| `-temp_dir <path>` | `/tmp` | scratch dir for intermediate JSON |

If `-schema` is omitted, `nextmap_runner.py` resolves the schema shipped with
the installed `nextmap` package, so the plugin works regardless of the working
directory once `nextmap` is installed.

## Demo scripts

- `run_demo.sh`, `fir_demo.sh` — end-to-end demos (adjust the `yosys` path at
  the top to your install).
- `scripts/*.ys` — standalone Yosys scripts replicating the notebook flows
  (retiming, DSP mapping, comprehensive optimization). See `scripts/README.md`.
