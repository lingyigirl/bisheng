"""Validated, atomic local cache for immutable skill bundle versions."""

import hashlib
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

MAX_FILES = 4096
MAX_PATH_BYTES = 512
MAX_UNPACKED_SIZE = 100 * 1024 * 1024
MAX_STORED_SIZE = 110 * 1024 * 1024
_gate = threading.Lock()
_locks = {}
_slots = threading.BoundedSemaphore(4)


class BundleTooLargeError(ValueError):
    """An unpacked bundle exceeds the supported resource budget."""


def safe_path(rel):
    raw = rel.replace("\\", "/")
    parts = raw.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (
        len(raw.encode("utf-8")) > MAX_PATH_BYTES
        or any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts)
        or any(any(ord(c) < 32 or c in ':*?"<>|' for c in p) for p in parts)
        or any(p.split(".")[0].upper() in reserved for p in parts)
    ):
        raise ValueError("illegal bundle path")
    return PurePosixPath(raw)


def validate_files(files):
    if "SKILL.md" not in files:
        raise ValueError("bundle must contain SKILL.md")
    if len(files) > MAX_FILES or sum(len(c) for c in files.values()) > MAX_UNPACKED_SIZE:
        raise BundleTooLargeError("skill bundle exceeds resource limits")
    seen = set()
    paths = []
    for rel in files:
        path = str(safe_path(rel)).casefold()
        if path in seen:
            raise ValueError("duplicate bundle path")
        seen.add(path)
        paths.append(path)
    for path in paths:
        if any(parent.as_posix().casefold() in seen for parent in PurePosixPath(path).parents if str(parent) != "."):
            raise ValueError("conflicting bundle paths")


def content_hash(files):
    validate_files(files)
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[rel]).digest())
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def bundle_lock(key):
    with _gate:
        lock, count = _locks.get(key, (threading.Lock(), 0))
        _locks[key] = (lock, count + 1)
    try:
        with lock, _slots:
            yield
    finally:
        with _gate:
            _, count = _locks[key]
            if count == 1:
                del _locks[key]
            else:
                _locks[key] = (lock, count - 1)


def read_directory(base: Path):
    if base.is_symlink() or not (base / "SKILL.md").is_file():
        raise FileNotFoundError("Skill bundle is missing")
    files = {}
    total = 0
    for item in sorted(base.rglob("*")):
        if item.is_symlink():
            raise ValueError("symbolic links are not allowed in skill bundles")
        if not item.is_file():
            continue
        rel = item.relative_to(base).as_posix()
        safe_path(rel)
        if total + item.stat().st_size > MAX_UNPACKED_SIZE or len(files) >= MAX_FILES:
            raise ValueError("skill bundle exceeds resource limits")
        with item.open("rb") as stream:
            data = stream.read(MAX_UNPACKED_SIZE - total + 1)
        total += len(data)
        if total > MAX_UNPACKED_SIZE:
            raise BundleTooLargeError("skill bundle exceeds resource limits")
        files[rel] = data
    validate_files(files)
    return files


def check_ancestors(path: Path, root: Path):
    if not path.is_relative_to(root):
        raise ValueError("invalid bundle directory")
    for parent in (path, *path.parents):
        if parent == root:
            break
        try:
            attrs = parent.lstat()
        except FileNotFoundError:
            continue
        if parent.is_symlink() or getattr(attrs, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked bundle directories are not allowed")


def invalidate(path: Path, root: Path):
    if not path.is_relative_to(root) or path == root:
        raise ValueError("invalid cache target")
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def install(path: Path, files, root: Path):
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid4().hex}")
    try:
        tmp.mkdir(parents=True, mode=0o700)
        for rel, data in files.items():
            target = tmp / safe_path(rel)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(data)
        try:
            tmp.replace(path)
        except OSError:
            # Another process may have published the same immutable version.
            if content_hash(read_directory(path)) != content_hash(files):
                raise
    finally:
        invalidate(tmp, root)
