"""Explicitly pre-download documented weights; never called by app startup."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.services.model_adapters import _ensure_weights, _local_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("quantity", "condition"))
    args = parser.parse_args()
    settings.model_offline = False
    filename = settings.quantity_checkpoint if args.kind == "quantity" else settings.condition_checkpoint
    _ensure_weights(_local_checkpoint(filename), args.kind)
    print(f"Cached {Path(filename).name}. Enable the corresponding model to use it.")


if __name__ == "__main__":
    main()
