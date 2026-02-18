cmake_minimum_required(VERSION 3.10)
project(telemetry_collector C)

set(CMAKE_C_STANDARD 99)

# Output binaries directly into the build directory
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})

# Add source directory
add_subdirectory(src)