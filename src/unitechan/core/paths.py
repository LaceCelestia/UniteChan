from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / 'pyproject.toml').exists():
            return parent
    return Path.cwd()


def data_dir() -> Path:
    configured = os.getenv('UNITECHAN_DATA_DIR')
    if configured:
        return Path(configured).expanduser()
    return project_root() / 'data'


def data_path(name: str) -> Path:
    return data_dir() / name
