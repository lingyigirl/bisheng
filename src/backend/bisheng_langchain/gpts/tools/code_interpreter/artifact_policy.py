"""Decide which code-interpreter files become durable workspace state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bisheng_langchain.gpts.tools.code_interpreter.base_executor import (
    OUTPUT_DIR_NAME,
    SCRATCH_DIR_NAME,
    is_generated_font_cache_path,
    is_skipped_workspace_name,
)

OUTPUT_DIR = OUTPUT_DIR_NAME
SCRATCH_DIR = SCRATCH_DIR_NAME

# These are regenerable tool byproducts only when they live under scratch/.
SCRATCH_TRANSIENT_DIRS = frozenset(
    {
        "build",
        "coverage",
        "dist",
        "fonts",
        "playwright-report",
        "target",
        "test-results",
    }
)
SCRATCH_TRANSIENT_SUFFIXES = frozenset(
    {
        ".aux",
        ".fdb_latexmk",
        ".fls",
        ".out",
        ".part",
        ".synctex.gz",
        ".temp",
        ".tmp",
        ".toc",
    }
)


class ArtifactDisposition(StrEnum):
    PERSIST = "persist"
    SANDBOX_ONLY = "sandbox_only"
    PRUNE = "prune"


@dataclass(frozen=True)
class ArtifactDecision:
    disposition: ArtifactDisposition
    reason: str
    is_product: bool


def normalize_workspace_path(path: str) -> str:
    """Return a workspace-relative POSIX path for sandbox or host inputs."""
    normalized = (path or "").strip().replace("\\", "/").lstrip("/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("home/user/"):
        normalized = normalized[len("home/user/") :]

    zones = ("output", "scratch", "uploads", "skills")
    is_workspace_relative = any(normalized == zone or normalized.startswith(zone + "/") for zone in zones)
    if not is_workspace_relative:
        # Fold Windows/Linux executor host paths to the workspace-relative tail.
        for zone in zones:
            marker = f"/{zone}/"
            marker_index = normalized.rfind(marker)
            if marker_index >= 0:
                normalized = normalized[marker_index + 1 :]
                break
    return normalized.rstrip("/")


def is_product_path(path: str) -> bool:
    normalized = normalize_workspace_path(path)
    return normalized == OUTPUT_DIR or normalized.startswith(OUTPUT_DIR + "/")


def classify_workspace_path(path: str) -> ArtifactDecision:
    """Classify a sandbox path without reading its file content.

    Priority is deliberate: output/ is the protected delivery zone, then
    toolchain directories are pruned, then explicitly regenerable scratch
    byproducts remain sandbox-only. Every unknown path stays durable.
    """
    normalized = normalize_workspace_path(path)
    if not normalized:
        return ArtifactDecision(ArtifactDisposition.PERSIST, "unknown_root", False)

    is_product = is_product_path(normalized)
    if is_product:
        return ArtifactDecision(ArtifactDisposition.PERSIST, "output", True)

    segments = normalized.split("/")
    if any(is_skipped_workspace_name(segment) for segment in segments):
        return ArtifactDecision(ArtifactDisposition.PRUNE, "toolchain_cache", False)

    if normalized == SCRATCH_DIR or normalized.startswith(SCRATCH_DIR + "/"):
        scratch_segments = segments[1:]
        if any(segment in SCRATCH_TRANSIENT_DIRS for segment in scratch_segments):
            return ArtifactDecision(ArtifactDisposition.SANDBOX_ONLY, "scratch_transient_dir", False)
        if is_generated_font_cache_path(normalized):
            return ArtifactDecision(ArtifactDisposition.SANDBOX_ONLY, "scratch_font_cache", False)
        basename = segments[-1]
        if basename.startswith("~$") or any(basename.endswith(suffix) for suffix in SCRATCH_TRANSIENT_SUFFIXES):
            return ArtifactDecision(ArtifactDisposition.SANDBOX_ONLY, "scratch_transient_file", False)

    return ArtifactDecision(ArtifactDisposition.PERSIST, "unknown_task_state", False)


def should_prune_workspace_path(path: str) -> bool:
    return classify_workspace_path(path).disposition is ArtifactDisposition.PRUNE


def is_sandbox_only_path(path: str) -> bool:
    return classify_workspace_path(path).disposition is ArtifactDisposition.SANDBOX_ONLY
