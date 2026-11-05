"""Bench tests choose their schema module explicitly (test_shipped.py spawns subprocesses with shipped_schema);
the in-process tests always use the private schema, whatever the shell exported."""
import os

os.environ.pop("BENCH_SCHEMA_MODULE", None)
