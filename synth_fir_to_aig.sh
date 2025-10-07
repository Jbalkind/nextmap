#!/bin/bash
# Synthesize FIR design to AIG format for LUT mapping testing

echo "Converting FIR n16_w8 to AIG format..."

yosys -p "
read_json eval/out/fir_n16_w8.json;
flatten;
techmap;
abc -g AND;
clean;
write_json eval/out/fir_n16_w8_aig.json;
" 2>&1 | grep -E "(Executing|Writing|selected|cells)"

echo "Done! Output: eval/out/fir_n16_w8_aig.json"
