#!/bin/bash
# Build the optional emapcc C++ accelerator (native group_wires / prune_cells
# used by the ILP extraction path). This is OPTIONAL: nextmap falls back to a
# pure-Python implementation if the compiled module is absent.
#
# Requires: cmake, a C++17 compiler, and pybind11 (`pip install pybind11`).
# The resulting emapcc*.so lands in this directory's build/ and is imported as
# nextmap.emapcc.build.emapcc at runtime.
set -e
cd "$(dirname "$0")"
mkdir -p build
cd build
# Help CMake find pybind11's CMake config when installed via pip.
PYBIND11_DIR="$(python3 -c 'import pybind11; print(pybind11.get_cmake_dir())' 2>/dev/null || true)"
cmake .. ${PYBIND11_DIR:+-Dpybind11_DIR="$PYBIND11_DIR"}
make
echo "Built: $(ls emapcc*.so 2>/dev/null || echo '(no .so produced)')"
