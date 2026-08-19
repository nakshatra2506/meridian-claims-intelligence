import sys
sys.path.insert(0, ".")
from backend.data.curated_loader import find_curated_dir
from backend.data import warehouse as wh

print("curated dir :", find_curated_dir())
print("source      :", wh.source())
print("tables      :", sorted(wh.tables()))