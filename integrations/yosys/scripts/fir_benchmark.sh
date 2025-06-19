#!/bin/bash

# FIR Filter Benchmark Script
# Automated evaluation of FIR designs with different configurations
# Based on eval_fir_v2.ipynb

export PATH="$HOME/Documents/tools/yosys:$PATH"

echo "=== FIR Filter Nextmap Benchmark ==="
echo

# Create output directory
mkdir -p ../eval/out

# Array of FIR designs to test (adjust paths as needed)
declare -a designs=(
    "../eval/fir/fir_n16_w8.v"
    "../eval/fir/fir_n16_w16.v"
    "../eval/fir/fir_n16_w32.v"
    "../eval/fir/fir_n32_w32.v"
    "../eval/fir/fir_n64_w32.v"
)

# Function to extract statistics from Yosys output
extract_stats() {
    local file=$1
    if [ -f "$file" ]; then
        echo "Statistics from $file:"
        grep -E "(cells|wires|LUT|DSP|BRAM)" "$file" | head -10
    else
        echo "Statistics file $file not found"
    fi
    echo
}

# Function to run benchmark on a single design
benchmark_design() {
    local design_file=$1
    local design_name=$(basename "$design_file" .v)

    echo "=== Benchmarking: $design_name ==="

    if [ ! -f "$design_file" ]; then
        echo "Warning: Design file $design_file not found, skipping..."
        return
    fi

    # Original design synthesis
    echo "1. Running standard Xilinx synthesis..."
    yosys -m ../nextmap_plugin_simple.so -q -p "
        read_verilog $design_file
        proc
        opt_merge
        opt_clean
        write_json ../eval/out/${design_name}.json
        synth_xilinx -family xcup
        tee -o ../eval/out/${design_name}.stat stat
    " 2>/dev/null

    if [ $? -ne 0 ]; then
        echo "Error: Standard synthesis failed for $design_name"
        return
    fi

    # Nextmap DSP optimization
    echo "2. Running nextmap DSP optimization..."
    yosys -m ../nextmap_plugin_simple.so -q -p "
        read_verilog $design_file
        proc
        opt_merge
        opt_clean
        nextmap -strategy dsp
        write_verilog ../eval/out/${design_name}_nextmap.v
        write_json ../eval/out/${design_name}_nextmap.json
        synth_xilinx -family xcup
        tee -o ../eval/out/${design_name}_nextmap.stat stat
    " 2>/dev/null

    if [ $? -ne 0 ]; then
        echo "Error: Nextmap synthesis failed for $design_name"
        return
    fi

    # Compare results
    echo "3. Results comparison:"
    echo "   Original design:"
    extract_stats "../eval/out/${design_name}.stat"

    echo "   Nextmap optimized:"
    extract_stats "../eval/out/${design_name}_nextmap.stat"

    echo "----------------------------------------"
}

# Main benchmark loop
for design in "${designs[@]}"; do
    benchmark_design "$design"
done

echo "=== BENCHMARK SUMMARY ==="
echo "Results saved in ../eval/out/ directory:"
echo "  - *.stat files contain synthesis statistics"
echo "  - *_nextmap.* files contain optimized designs"
echo "  - Compare DSP and LUT utilization between original and optimized"
echo
echo "To analyze results:"
echo "  grep -E 'DSP|LUT' ../eval/out/*.stat"
echo
echo "=== BENCHMARK COMPLETE ==="