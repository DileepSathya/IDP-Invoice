"""Shared PyInstaller collection helpers for Paddle/PaddleOCR."""

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# Full collect_all — required for native/data-heavy OCR stacks.
COLLECT_ALL_PACKAGES = (
    "paddle",
    "paddleocr",
    "Cython",
    "setuptools",
    "imgaug",
    "pyclipper",
    "shapely",
    "lmdb",
    "skimage",
    "scipy",
    "imageio",
)

# Hidden imports only — avoid pulling torch/Qt via matplotlib collect_all.
HIDDEN_IMPORT_PACKAGES = (
    "rapidfuzz",
    "imageio",
    "lazy_loader",
    "matplotlib",
    "yaml",
    "bs4",
    "lxml",
    "fontTools",
    "fire",
    "openpyxl",
    "premailer",
    "fitz",
    "docx",
    "PIL",
    "cv2",
)


def _extend_package(
    datas: list,
    binaries: list,
    hiddenimports: list,
    package: str,
) -> None:
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas.extend(pkg_datas)
        binaries.extend(pkg_binaries)
        hiddenimports.extend(pkg_hidden)
    except Exception:
        pass

    try:
        datas.extend(collect_data_files(package, include_py_files=True))
    except Exception:
        pass


def get_paddle_artifacts() -> tuple[list, list, list]:
    import paddle

    datas: list = []
    binaries: list = []
    hiddenimports: list = []

    for package in COLLECT_ALL_PACKAGES:
        _extend_package(datas, binaries, hiddenimports, package)

    for package in HIDDEN_IMPORT_PACKAGES:
        try:
            hiddenimports.extend(collect_submodules(package))
        except Exception:
            pass
        hiddenimports.append(package)

    for lib_binaries in collect_dynamic_libs("paddle"):
        binaries.append(lib_binaries)

    for pkg in ("cv2", "pyclipper", "shapely", "lmdb"):
        try:
            binaries.extend(collect_dynamic_libs(pkg))
        except Exception:
            pass

    paddle_root = Path(paddle.__file__).resolve().parent
    libs_dir = paddle_root / "libs"
    if libs_dir.is_dir():
        for dll in libs_dir.glob("*.dll"):
            binaries.append((str(dll), "paddle/libs"))

    base_pyd = paddle_root / "base" / "libpaddle.pyd"
    if base_pyd.is_file():
        binaries.append((str(base_pyd), "paddle/base"))

    try:
        import Cython

        utility_dir = Path(Cython.__file__).resolve().parent / "Utility"
        if utility_dir.is_dir():
            for item in utility_dir.rglob("*"):
                if item.is_file():
                    rel_parent = item.parent.relative_to(utility_dir.parent)
                    datas.append((str(item), str(Path("Cython") / rel_parent).replace("\\", "/")))
    except Exception:
        pass

    site_packages = Path(paddle.__file__).resolve().parent.parent
    for metadata_pkg in (
        "imageio",
        "imgaug",
        "scikit-image",
        "paddleocr",
        "paddlepaddle",
        "Pillow",
        "matplotlib",
        "lazy_loader",
        "PyYAML",
        "lxml",
        "shapely",
        "pyclipper",
    ):
        try:
            datas.extend(copy_metadata(metadata_pkg))
        except Exception:
            pass

    for dist_info in site_packages.glob("*.dist-info"):
        datas.append((str(dist_info), dist_info.name))

    for pkg_name in ("imageio", "lazy_loader", "yaml"):
        pkg_dir = site_packages / pkg_name
        if pkg_dir.is_dir():
            datas.append((str(pkg_dir), pkg_name))

    hiddenimports.extend(
        [
            "paddle",
            "paddle.base",
            "paddle.base.core",
            "paddleocr",
            "paddleocr.paddleocr",
            "paddleocr.tools",
            "paddleocr.ppocr",
            "imghdr",
            "pyclipper",
            "shapely",
            "shapely.geometry",
            "lmdb",
            "skimage",
            "scipy",
            "imgaug",
            "imgaug.augmenters",
            "imageio",
            "yaml",
            "bs4",
            "lxml",
            "Cython",
            "Cython.Compiler",
            "Cython.Compiler.Main",
            "Cython.Compiler.Code",
            "setuptools",
            "setuptools.command.build_ext",
        ]
    )

    return datas, binaries, hiddenimports
