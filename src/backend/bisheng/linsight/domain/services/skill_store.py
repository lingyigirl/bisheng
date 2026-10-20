"""Object-storage skill persistence with legacy read-only migration support."""

from __future__ import annotations

import io
import re
import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import yaml
from pypinyin import lazy_pinyin

from bisheng.common.services.config_service import settings as bisheng_settings
from bisheng.core.cache.utils import CACHE_DIR
from bisheng.core.storage.minio.minio_manager import get_minio_storage_sync
from bisheng.linsight.domain.services.skill_bundle_cache import (
    MAX_FILES,
    MAX_STORED_SIZE,
    BundleTooLargeError,
    bundle_lock,
    check_ancestors,
    content_hash,
    install,
    invalidate,
    read_directory,
    safe_path,
    validate_files,
)

SKILL_MD = "SKILL.md"
BUILTIN_DIR = "built-in"
TENANT_SKILLS_DIR = "data/skills"

# Upload payload limit: the .md / .zip / .skill bytes that arrive over HTTP.
MAX_BUNDLE_SIZE = 10 * 1024 * 1024
# Unpacked limit: sum of every extracted file's contents (also the GitHub import's
# total download size). Deliberately larger than the upload limit — an archive of
# pptx templates/fonts/images compresses well and expands past 10MB while the .zip
# itself is far below it. This line is the zip-bomb guard, not a second copy of the
# upload limit. (deepagents' MAX_SKILL_FILE_SIZE is a per-SKILL.md cap, unrelated.)
MAX_UNPACKED_SIZE = 100 * 1024 * 1024
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_DISPLAY_NAME_LEN = 255

DISPLAY_NAME_META_KEY = "display-name"
SOP_ID_META_KEY = "sop-id"

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def slugify_pinyin(text: str, max_len: int = MAX_NAME_LEN) -> str:
    """Build a deepagents-legal skill name from arbitrary (Chinese) text.

    ASCII letters/digits are kept lowercased, CJK characters become pinyin
    syllables, everything else collapses into single hyphens.
    Returns "" when nothing survives — caller decides the fallback name.
    """
    parts: list[str] = []
    for ch in text:
        if ch.isascii() and ch.isalnum():
            parts.append(ch.lower())
        elif ch.isascii():
            parts.append("-")
        else:
            # lazy_pinyin echoes characters it cannot transliterate (e.g. full-width
            # punctuation) — only accept pure-ASCII alphanumeric syllables.
            py = lazy_pinyin(ch)
            syllable = py[0].strip().lower() if py else ""
            parts.append(f"-{syllable}-" if syllable.isascii() and syllable.isalnum() else "-")
    slug = re.sub(r"-+", "-", "".join(parts)).strip("-")
    slug = slug[:max_len].rstrip("-")
    # Truncation may leave a trailing fragment producing "--"; normalize again.
    return re.sub(r"-+", "-", slug)


def validate_skill_name(name: str) -> str | None:
    """Return an error message when ``name`` violates the spec, else None.

    Mirrors deepagents ``_validate_skill_name`` so a skill we accept is always
    loadable by the middleware.
    """
    if not name:
        return "name is required"
    if len(name) > MAX_NAME_LEN:
        return f"name exceeds {MAX_NAME_LEN} characters"
    if not _NAME_RE.match(name):
        return "name must be lowercase alphanumeric with single hyphens only"
    return None


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split SKILL.md into (frontmatter dict, body). Raises ValueError when malformed."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("missing YAML frontmatter (--- block) in SKILL.md")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return meta, text[match.end() :]


def render_skill_md(meta: dict, body: str) -> str:
    """Render SKILL.md from an arbitrary frontmatter mapping + body.

    Keeps every frontmatter key as-is (document order preserved) — used when an
    imported bundle's frontmatter must be rewritten without dropping foreign keys
    like ``license`` or ``allowed-tools``.
    """
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip("\n")
    return f"---\n{front}\n---\n\n{body.strip()}\n"


def compose_skill_md(
    name: str,
    description: str,
    body: str,
    display_name: str | None = None,
    allowed_tools: str | None = None,
    extra_metadata: dict[str, str] | None = None,
) -> str:
    """Render a canonical SKILL.md from structured fields (form-create path)."""
    meta: dict = {"name": name, "description": description}
    if allowed_tools:
        meta["allowed-tools"] = allowed_tools
    metadata: dict[str, str] = {}
    if display_name:
        metadata[DISPLAY_NAME_META_KEY] = display_name
    if extra_metadata:
        metadata.update({k: str(v) for k, v in extra_metadata.items()})
    if metadata:
        meta["metadata"] = metadata
    return render_skill_md(meta, body)


def unpack_zip_bytes(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_STORED_SIZE:
        raise ValueError("skill archive exceeds resource limits")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if len(entries) > MAX_FILES or sum(info.file_size for info in entries) > MAX_UNPACKED_SIZE:
                raise BundleTooLargeError("skill bundle exceeds resource limits")
            files = {}
            for info in entries:
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("symbolic links are not allowed")
                rel = info.orig_filename
                if "\0" in rel:
                    raise ValueError("illegal bundle path")
                if not info.flag_bits & 0x800:
                    try:
                        rel = rel.encode("cp437").decode("utf-8")
                    except (UnicodeError, LookupError):
                        rel = info.orig_filename
                if "\0" in rel:
                    raise ValueError("illegal bundle path")
                path = str(safe_path(rel))
                if path.startswith("__MACOSX/") or PurePosixPath(path).name == ".DS_Store":
                    continue
                if path in files:
                    raise ValueError("duplicate bundle path")
                files[path] = archive.read(info)
        if "SKILL.md" not in files:
            tops = {p.split("/")[0] for p in files}
            if len(tops) == 1 and all("/" in p for p in files):
                prefix = next(iter(tops)) + "/"
                files = {p[len(prefix) :]: value for p, value in files.items()}
        validate_files(files)
        return files
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise ValueError("invalid zip archive") from exc


def pack_bundle_zip(files):
    validate_files(files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[rel])
    return buffer.getvalue()


_safe_rel_path = safe_path


class BundleRef(NamedTuple):
    size: int
    content_hash: str
    object_key: str


class SkillStore:
    """MinIO is authoritative; local immutable directories are disposable caches."""

    def __init__(self, root=None, minio=None, legacy_root=None):
        conf = bisheng_settings.get_linsight_conf() if root is None else None
        self.root = Path(root or conf.skills_cache_dir or Path(CACHE_DIR) / "linsight_skills").resolve()
        self.legacy_root = Path(legacy_root or (root if root is not None else conf.skills_root)).resolve()
        self._minio = minio

    @property
    def minio(self):
        if self._minio is None:
            self._minio = get_minio_storage_sync()
        return self._minio

    @staticmethod
    def _identity(tenant_id, name):
        if not isinstance(tenant_id, int) or tenant_id < 1 or validate_skill_name(name):
            raise ValueError("invalid skill identity")

    def object_path(self, tenant_id, name):
        self._identity(tenant_id, name)
        return f"data/skills/{tenant_id}/{name}"

    def object_key(self, tenant_id, name, version):
        self._identity(tenant_id, name)
        if not re.fullmatch(r"[a-f0-9]{64}", version or ""):
            raise ValueError("invalid skill version")
        return f"linsight/skills/{tenant_id}/{name}/{version}.zip"

    def cache_dir(self, tenant_id, name, version):
        self.object_key(tenant_id, name, version)
        return self.root / str(tenant_id) / name / version

    def builtin_dir(self):
        return self.legacy_root / BUILTIN_DIR

    def tenant_dir(self, tenant_id):
        if not isinstance(tenant_id, int) or tenant_id < 1:
            raise ValueError("invalid tenant")
        return self.legacy_root / TENANT_SKILLS_DIR / str(tenant_id)

    def skill_dir(self, tenant_id, name):
        self._identity(tenant_id, name)
        return self.tenant_dir(tenant_id) / name

    def _download(self, tenant_id, name, version):
        key = self.object_key(tenant_id, name, version)
        try:
            size = self.minio.get_object_size_sync(bucket_name=self.minio.bucket, object_name=key)
            if size > MAX_STORED_SIZE:
                raise ValueError("skill archive exceeds resource limits")
            stream = self.minio.download_object_sync(bucket_name=self.minio.bucket, object_name=key)
        except Exception as exc:
            if getattr(exc, "code", None) in ("NoSuchKey", "NoSuchObject"):
                raise FileNotFoundError("Skill bundle object is missing") from exc
            raise
        try:
            data = stream.read(MAX_STORED_SIZE + 1)
        finally:
            stream.close()
            stream.release_conn()
        files = unpack_zip_bytes(data)
        if content_hash(files) != version:
            raise ValueError("skill bundle integrity check failed")
        return files

    def load_bundle(self, tenant_id, name, version=None):
        self._identity(tenant_id, name)
        if not version:
            base = self.skill_dir(tenant_id, name)
            check_ancestors(base, self.legacy_root)
            return read_directory(base)
        dst = self.cache_dir(tenant_id, name, version)
        check_ancestors(dst, self.root)
        with bundle_lock(str(dst)):
            try:
                files = read_directory(dst)
                if content_hash(files) == version:
                    return files
            except (FileNotFoundError, ValueError):
                # Cache is optional: corruption is repaired from the authority.
                files = None
            invalidate(dst, self.root)
            files = self._download(tenant_id, name, version)
            install(dst, files, self.root)
            return files

    def write_bundle(self, tenant_id, name, files):
        version = content_hash(files)
        key = self.object_key(tenant_id, name, version)
        dst = self.cache_dir(tenant_id, name, version)
        check_ancestors(dst, self.root)
        with bundle_lock(str(dst)):
            self.minio.put_object_sync(bucket_name=self.minio.bucket, object_name=key, file=pack_bundle_zip(files))
            verified = self._download(tenant_id, name, version)
            if dst.exists():
                try:
                    valid = content_hash(read_directory(dst)) == version
                except (ValueError, FileNotFoundError):
                    valid = False
                if not valid:
                    invalidate(dst, self.root)
            install(dst, verified, self.root)
        return BundleRef(sum(map(len, files.values())), version, key)

    def invalidate_cache(self, tenant_id, name, version):
        dst = self.cache_dir(tenant_id, name, version)
        check_ancestors(dst, self.root)
        with bundle_lock(str(dst)):
            invalidate(dst, self.root)

    def exists(self, tenant_id, name, content_hash=None):
        try:
            self.load_bundle(tenant_id, name, content_hash)
            return True
        except FileNotFoundError:
            return False

    def read_text(self, tenant_id, name, rel=SKILL_MD, content_hash=None):
        return self.read_bytes(tenant_id, name, rel, content_hash).decode("utf-8", errors="replace")

    def read_bytes(self, tenant_id, name, rel, content_hash=None):
        path = str(safe_path(rel))
        files = self.load_bundle(tenant_id, name, content_hash)
        if path not in files:
            raise FileNotFoundError("file not found in skill bundle")
        return files[path]

    def list_files(self, tenant_id, name, content_hash=None):
        try:
            files = self.load_bundle(tenant_id, name, content_hash)
        except FileNotFoundError:
            return []
        return [{"path": path, "size": len(files[path])} for path in sorted(files, key=lambda p: (p != SKILL_MD, p))]

    def delete(self, tenant_id, name):
        # Metadata revokes access. Immutable objects and legacy sources are retained
        # until an explicit, quiesced maintenance operation verifies references.
        self._identity(tenant_id, name)
        return False
