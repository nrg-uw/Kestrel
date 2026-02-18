cmake_minimum_required(VERSION 3.13)
project(int_xd_p4 C)

# Hard-code SDE location so don’t need to pass -DSDE each time
set(SDE "/home/n6saha/bf-sde-9.13.4")

# P4 program info
set(P4_NAME "int_xd")
set(P4_PATH "${CMAKE_SOURCE_DIR}/int_xd.p4")
set(P4_LANG "p4-16")

# Architecture defaults: Tofino ON, Tofino2 OFF
set(TOFINO  ON)
set(TOFINO2 OFF)

# Add any local include dirs program needs
set(P4PPFLAGS "-I${CMAKE_SOURCE_DIR} -I${CMAKE_SOURCE_DIR}/includes")
set(P4FLAGS   "-v -g")

# Optional: PD/thrift artifacts (usually OFF in modern BF-RT setups)
set(WITHPD        OFF)
set(THRIFT-DRIVER OFF)

# Point CMake at the SDE’s modules and install tree
set(CMAKE_MODULE_PATH "${SDE}/cmake" ${CMAKE_MODULE_PATH})
set(CMAKE_INSTALL_PREFIX "${SDE}/install" CACHE PATH "SDE install prefix" FORCE)

# Delegate actual build to the SDE helper
add_subdirectory("${SDE}/p4studio" "${CMAKE_BINARY_DIR}/_p4studio")

# Print a summary at configure time
message(STATUS "Building ${P4_NAME} from ${P4_PATH} for Tofino (SDE=${SDE})")

