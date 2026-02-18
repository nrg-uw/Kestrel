#!/bin/bash
# Builds telemetry collector binary
mkdir -p build
cd build
if [ ! -f "Makefile" ] || [ "../CMakeLists.txt" -nt "Makefile" ]; then
    cmake ../
fi
make -j4
cd ..