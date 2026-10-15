"""Configuration commune des tests : rend le dossier src/ importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
