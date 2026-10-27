"""Entry point for PyInstaller (the package uses relative imports, so it can't be the script)."""
import sys

from dso_enroller.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
