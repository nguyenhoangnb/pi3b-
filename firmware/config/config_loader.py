from pathlib import Path
import yaml

def load(path: str | Path) -> dict:
    """Read a YAML file -> dict. Raises if file missing/invalid."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data
