#!/usr/bin/env python3
"""Convenience launcher so the app can be started without -m."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dso_app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
