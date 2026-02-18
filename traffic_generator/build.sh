#!/bin/bash

set -e

# Initialize Go module if it doesn't exist
if [ ! -f "go.mod" ]; then
    echo "Initializing Go module..."
    go mod init traffic_generator
fi

# Download dependencies
echo "Downloading dependencies..."
go mod tidy

# Build
mkdir -p build
go build -o build/traffic_generator traffic_generator.go

if [ -f "build/traffic_generator" ]; then
    echo "Build successful: build/traffic_generator"
else
    echo "Build failed"
    exit 1
fi