#!/bin/bash
# Runs as root at container start.
# Creates volume-mounted directories and transfers ownership to appuser,
# then drops privileges and exec's the main process.
set -e

mkdir -p /app/watch /app/output /app/data
chown -R appuser:appuser /app/watch /app/output /app/data

exec gosu appuser "$@"
