#!/usr/bin/env python3
"""CLI utility to inspect and extract structured shipping label fields from an image.

Prints the fixed eight summary fields:
Platform: <value>
Courier: <value>
Order ID: <value>
AWB/Tracking number: <value>
PIN code: <value>
Product: <value>
Colour/Model: <value>
Quantity: <value>
"""

import argparse
import json
from pathlib import Path
import sys

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.label_parser import build_label_summary, parse_shipping_label
from app.services.ocr_service import extract_text


def format_field_val(val) -> str:
    """Format None as 'None' or empty, or string value."""
    if val is None:
        return "None"
    return str(val)


def inspect_image(image_path: Path, output_json: bool = False) -> int:
    if not image_path.exists():
        print(f"Error: Image file not found at '{image_path}'", file=sys.stderr)
        return 1

    detections = extract_text(image_path)
    parsed = parse_shipping_label(detections)
    summary = build_label_summary(parsed)

    if output_json:
        print(json.dumps(summary.model_dump(), indent=2))
    else:
        print(f"Platform: {format_field_val(summary.platform)}")
        print(f"Courier: {format_field_val(summary.courier)}")
        print(f"Order ID: {format_field_val(summary.order_id)}")
        print(f"AWB/Tracking number: {format_field_val(summary.tracking_number)}")
        print(f"PIN code: {format_field_val(summary.pin_code)}")
        print(f"Product: {format_field_val(summary.product)}")
        print(f"Colour/Model: {format_field_val(summary.colour_model)}")
        print(f"Quantity: {format_field_val(summary.quantity)}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Inspect and extract 8 structured fields from a shipping label image.")
    parser.add_argument("image_path", nargs="?", type=str, help="Path to shipping label image file.")
    parser.add_argument("--json", action="store_true", help="Output summary as formatted JSON.")
    args = parser.parse_args()

    target_path = None
    if args.image_path:
        p = Path(args.image_path)
        if p.exists():
            target_path = p
        elif (backend_dir / args.image_path).exists():
            target_path = backend_dir / args.image_path
        else:
            target_path = p
    else:
        # Default fallback to known demonstration image if available
        default_candidates = [
            backend_dir / "storage" / "uploads" / "8f492c8431ba4e4f8184aeea688b9a92.jpeg",
            backend_dir / "storage" / "uploads" / "c2e7252b83aa43c3a4a643a08c3e2e5e.jpeg",
        ]
        for candidate in default_candidates:
            if candidate.exists():
                target_path = candidate
                break

    if not target_path or not target_path.exists():
        print("Usage: python backend/scripts/inspect_label.py <path_to_image> [--json]", file=sys.stderr)
        print("Error: No image path provided and default sample not found.", file=sys.stderr)
        return 1

    return inspect_image(target_path, output_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
