"""
PyInstaller runtime hook: expose Paddle/OpenCV native DLL folders before imports.

Must run before paddle/paddleocr are imported.
"""

from __future__ import annotations

import os
import sys


def _add_search_path(path: str) -> None:
    if not os.path.isdir(path):
        return
    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(path)


if getattr(sys, "frozen", False):
    _base = getattr(sys, "_MEIPASS", "")
    if _base:
        os.environ.setdefault("FLAGS_prim_all", "0")
        os.environ.setdefault("FLAGS_prim_forward", "0")
        os.environ.setdefault("FLAGS_prim_backward", "0")
        for _subdir in (
            os.path.join("paddle", "libs"),
            os.path.join("paddle", "base"),
            "cv2",
        ):
            _add_search_path(os.path.join(_base, _subdir))
