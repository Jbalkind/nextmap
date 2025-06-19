# Nextmap Yosys Scripts

This directory contains Yosys scripts that replicate and extend the functionality from `demo.ipynb` using the nextmap plugin.

## Available Scripts

### `retiming_demo.ys`
Demonstrates DFF retiming optimization to reduce flip-flop count. Based on the `bad_multiplier` example from the original notebook.

**Usage:**
```bash
yosys -m ../nextmap_plugin_simple.so -s retiming_demo.ys
```

**What it does:**
- Loads `tests/bad_multiplier.v`
- Applies basic synthesis steps
- Runs nextmap with retiming strategy
- Shows before/after statistics
- Saves optimized design

### `dsp_mapping_demo.ys`
Technology mapping demo for DSP blocks. Based on the `dot_product` example.

**Usage:**
```bash
yosys -m ../nextmap_plugin_simple.so -s dsp_mapping_demo.ys
```

**What it does:**
- Loads `tests/dot_product.v`
- Applies nextmap DSP mapping optimization
- Compares with standard Xilinx synthesis
- Shows resource utilization differences

### `general_optimization.ys`
Comprehensive optimization using multiple rewrite rules including commutativity, associativity, and retiming.

**Usage:**
```bash
yosys -m ../nextmap_plugin_simple.so -s general_optimization.ys input_design.v
```

**What it does:**
- Accepts any Verilog file as input
- Applies comprehensive optimization strategy
- Shows optimization results
- Outputs `optimized_design.v` and `optimized_design.json`

## Strategy Options

The nextmap plugin supports three optimization strategies:

### `basic` (default)
- Simple DFF forward rewrite
- Minimal optimization
- Fast execution

### `retiming`
- Focus on DFF retiming optimizations
- Reduces flip-flop count
- Good for sequential circuits

### `comprehensive`
- All available rewrite rules:
  - Commutativity transformations
  - Associativity (left and right)
  - DFF forward and backward retiming
- Maximum optimization potential
- Longer execution time

## Manual Usage Examples

### Using strategy options directly:
```bash
# Basic optimization
yosys -m ./nextmap_plugin_simple.so -p "read_verilog design.v; prep; nextmap; stat"

# Retiming optimization
yosys -m ./nextmap_plugin_simple.so -p "read_verilog design.v; prep; nextmap -strategy retiming; stat"

# Comprehensive optimization
yosys -m ./nextmap_plugin_simple.so -p "read_verilog design.v; prep; nextmap -strategy comprehensive; stat"
```

### Custom parameters:
```bash
# Custom iterations and schema
yosys -m ./nextmap_plugin_simple.so -p "read_verilog design.v; prep; nextmap -strategy comprehensive -iterations 20 -schema ./custom_schema.sql; stat"
```

## Integration with Synthesis Flows

Add nextmap to existing synthesis scripts:

```tcl
# Before technology mapping
read_verilog $::env(DESIGN_TOP).v
hierarchy -top $::env(DESIGN_TOP)
proc
opt
nextmap -strategy comprehensive
techmap
abc -liberty $::env(LIBERTY_FILE)
write_verilog netlist.v
```

## Expected Results

- **Retiming**: Reduces DFF count by moving registers across combinational logic
- **Comprehensive**: May reduce overall cell count through algebraic simplification
- **Technology mapping**: Maps arithmetic patterns to specialized blocks (DSPs, etc.)

The actual optimizations depend on the input design structure and available rewrite patterns in the nextmap framework.