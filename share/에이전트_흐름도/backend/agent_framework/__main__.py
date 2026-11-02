"""Run the developer CLI without importing the product application."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
