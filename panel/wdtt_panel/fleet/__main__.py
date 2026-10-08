"""Точка входа: ``python -m wdtt_panel.fleet ...``."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
