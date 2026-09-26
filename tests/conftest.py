import sys
from pathlib import Path

# пакет лежит в src/ — добавляем в путь без установки
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
