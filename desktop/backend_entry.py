"""PyInstaller entry point for the bundled backend (relative imports need a package)."""
from jobhunt.__main__ import main

if __name__ == "__main__":
    main()
