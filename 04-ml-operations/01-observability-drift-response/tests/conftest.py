"""Torna os módulos do lab importáveis mesmo sem o PYTHONPATH do Makefile."""

import sys
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_ROOT / "src"))
