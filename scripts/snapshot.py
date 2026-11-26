"""Run daily (GitHub Actions): append today's fuel snapshot to data/fuel_history.json."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import sources

f = Path(__file__).resolve().parent.parent / "data" / "fuel_history.json"
hist = json.loads(f.read_text()) if f.exists() else []
snap = sources.fetch_fuel()
today = {"date": snap["as_of"], "prices": {k: v["price"] for k, v in snap["prices"].items()}}
if not hist or hist[-1]["prices"] != today["prices"] or hist[-1]["date"] != today["date"]:
    hist = [h for h in hist if h["date"] != today["date"]] + [today]
    f.write_text(json.dumps(hist, indent=1))
print("snapshots:", len(hist))
