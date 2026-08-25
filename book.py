#!/usr/bin/env python3
"""Thin launcher for the Book command-line interface."""

from booklib.cli import main
from booklib.v0 import prepare_new_case, revision_hash  # V0 public compatibility


if __name__ == "__main__":
    raise SystemExit(main())
