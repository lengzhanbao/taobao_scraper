"""Run the local panel without installing any web framework."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.control.server import main

if __name__ == "__main__":
    main()
