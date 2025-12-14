#!/usr/bin/env python3
import json
from pathlib import Path
import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/apply_patch.py patch.json")
        raise SystemExit(1)

    patch_path = Path(sys.argv[1])
    data = json.loads(patch_path.read_text(encoding="utf-8"))
    ops = data.get("ops", [])

    for op in ops:
        path = Path(op["path"])
        content = op.get("content", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"[write] {path}")

    print("[done] patch applied")

if __name__ == "__main__":
    main()
