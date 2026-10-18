"""Tenant custom skill management service (F035 Track D, design §7.4/§7.5).

Validation chain (design §7.4): size gate → unpack/parse → frontmatter +
name/display_name checks → duplicate checks → object bundle publication → DB metadata
→ best-effort owner tuple via PermissionService.authorize.

built-in skills never pass through here: any name that does not exist in the
tenant's `linsight_skill` table is answered with 11053 (not-found), so their
existence is not leaked (design §7.5).
"""

from __future__ import annotations

import asyncio
from typing import NamedTuple

from loguru import logger

from bisheng.common.errcode.linsight import (
    SkillBundleTooLargeError,
    SkillFileTooLargeError,
    SkillNameDuplicateError,
    SkillNotFoundError,
    SkillValidationError,
)
from bisheng.common.schemas.api import PageData
from bisheng.linsight.domain.models.linsight_skill import (
    SKILL_SOURCE_MANUAL,
    LinsightSkill,
    LinsightSkillDao,
)
from bisheng.linsight.domain.repositories.skill_repository import SkillRepository
from bisheng.linsight.domain.schemas.skill_schema import (
    SkillBrief,
    SkillCreateForm,
    SkillDetail,
    SkillFileContent,
    SkillFileEntry,
    SkillSelectable,
)
from bisheng.linsight.domain.services.github_skill_fetcher import fetch_skill_files, parse_github_url
from bisheng.linsight.domain.services.skill_bundle_cache import BundleTooLargeError, safe_path, validate_files
from bisheng.linsight.domain.services.skill_store import (
    DISPLAY_NAME_META_KEY,
    MAX_BUNDLE_SIZE,
    MAX_DESCRIPTION_LEN,
    MAX_DISPLAY_NAME_LEN,
    MAX_UNPACKED_SIZE,
    SKILL_MD,
    SkillStore,
    compose_skill_md,
    pack_bundle_zip,
    parse_skill_md,
    render_skill_md,
    slugify_pinyin,
    unpack_zip_bytes,
    validate_skill_name,
)
from bisheng.permission.domain.schemas.permission_schema import AuthorizeGrantItem
from bisheng.permission.domain.services.permission_service import PermissionService

_ARCHIVE_SUFFIXES = (".zip", ".skill")


class SkillMeta(NamedTuple):
    """Frontmatter fields extracted from an imported bundle.

    ``normalized_from`` carries the original frontmatter name when it was
    auto-normalized to a spec-legal skill ID, so the API can tell the user.
    """

    name: str
    display_name: str
    description: str
    normalized_from: str | None = None


SKILL_OBJECT_TYPE = "linsight_skill"


class SkillService:
    """All methods assume the tenant context is already established
    (endpoint dependency or worker context, design §6.5). Skills are
    tenant-private: ``LinsightSkillDao`` enforces strict ``tenant_id = current``
    isolation on every read/write (no Root→child sharing), so a tenant only
    ever sees and mutates its own skills."""

    def __init__(self, store: SkillStore | None = None, repository=None):
        self.store = store or SkillStore()
        self.repository = repository or SkillRepository(dao=LinsightSkillDao)

    # ------------------------------------------------------------- queries --
    async def get_page(
        self, keyword: str | None, enabled: bool | None, page: int, page_size: int
    ) -> PageData[SkillBrief]:
        skills, total = await self.repository.get_page(keyword=keyword, enabled=enabled, page=page, page_size=page_size)
        return PageData(data=[SkillBrief.from_model(s) for s in skills], total=total)

    async def get_selectable(self) -> list[SkillSelectable]:
        skills = await self.repository.enabled()
        return [
            SkillSelectable(name=s.name, display_name=s.display_name or s.name, description=s.description)
            for s in skills
        ]

    async def get_detail(self, tenant_id: int, name: str) -> SkillDetail:
        skill = await self._get_or_404(name)
        files = await self._load_existing_bundle(tenant_id, name, skill)
        source_text = files[SKILL_MD].decode("utf-8", errors="replace")
        _, body = parse_skill_md(source_text)
        detail = SkillDetail(
            **SkillBrief.from_model(skill).model_dump(),
            preview=body.strip(),
            source_text=source_text,
            files=[SkillFileEntry(path=p, size=len(files[p])) for p in sorted(files, key=lambda p: (p != SKILL_MD, p))],
        )
        return detail

    async def read_bundle_file(self, tenant_id: int, name: str, path: str) -> SkillFileContent:
        files = await self._load_existing_bundle(tenant_id, name)
        try:
            key = str(safe_path(path))
        except ValueError as exc:
            raise SkillValidationError() from exc
        if key not in files:
            raise SkillNotFoundError()
        content = files[key].decode("utf-8", errors="replace")
        return SkillFileContent(path=path, content=content)

    # ----------------------------------------------------------- mutations --
    async def create_from_form(self, tenant_id: int, user_id: int, form: SkillCreateForm) -> SkillDetail:
        files = {
            SKILL_MD: compose_skill_md(
                name=form.name,
                description=form.description,
                body=form.content,
                display_name=form.display_name,
            ).encode("utf-8")
        }
        return await self._create(tenant_id, user_id, form.name, form.display_name, form.description, files)

    async def create_from_upload(self, tenant_id: int, user_id: int, filename: str, data: bytes) -> SkillDetail:
        meta, files = await asyncio.to_thread(self._parse_upload, filename, data)
        detail = await self._create(tenant_id, user_id, meta.name, meta.display_name, meta.description, files)
        detail.normalized_from = meta.normalized_from
        return detail

    async def create_from_github(self, tenant_id: int, user_id: int, url: str) -> SkillDetail:
        """Import a skill from a public GitHub directory URL.

        The downloaded bundle joins the exact same create chain as the upload path,
        so frontmatter/size/duplicate checks and the owner tuple are all reused.
        """
        target = parse_github_url(url)
        files = await fetch_skill_files(target)
        meta = self._extract_meta(files)
        detail = await self._create(tenant_id, user_id, meta.name, meta.display_name, meta.description, files)
        detail.normalized_from = meta.normalized_from
        return detail

    async def update_from_form(self, tenant_id: int, name: str, form: SkillCreateForm) -> SkillDetail:
        skill = await self._get_or_404(name)
        if form.name != name:
            raise SkillValidationError(msg="skill ID cannot be changed when editing")
        self._validate_fields(form.name, form.display_name, form.description)
        await self._check_duplicate(form.name, form.display_name, exclude_id=skill.id)
        # Re-render SKILL.md, keep any other bundle assets untouched.
        new_md = compose_skill_md(
            name=form.name, description=form.description, body=form.content, display_name=form.display_name
        ).encode("utf-8")
        files = await self._load_existing_bundle(tenant_id, name, skill)
        files[SKILL_MD] = new_md
        await self._publish(skill, files, form.display_name, form.description)
        return await self.get_detail(tenant_id, name)

    async def update_from_upload(self, tenant_id: int, name: str, filename: str, data: bytes) -> SkillDetail:
        """Whole-bundle replacement; frontmatter name (after normalization) must equal the path name."""
        skill = await self._get_or_404(name)
        meta, files = await asyncio.to_thread(self._parse_upload, filename, data)
        if meta.name != name:
            raise SkillValidationError(msg=f"frontmatter name '{meta.name}' must equal skill ID '{name}'")
        await self._check_duplicate(name, meta.display_name, exclude_id=skill.id)
        await self._publish(skill, files, meta.display_name, meta.description)
        detail = await self.get_detail(tenant_id, name)
        detail.normalized_from = meta.normalized_from
        return detail

    async def set_status(self, name: str, enabled: bool) -> None:
        if not await self.repository.set_enabled(name, enabled):
            raise SkillNotFoundError()

    async def delete(self, tenant_id: int, name: str) -> None:
        skill = await self._get_or_404(name)
        await self.repository.delete(name)
        logger.info("skill access revoked: tenant={} skill_id={}", tenant_id, skill.id)

    # ----------------------------------------------------------- internals --
    async def _get_or_404(self, name: str) -> LinsightSkill:
        skill = await self.repository.get(name)
        if not skill:
            # built-in names also land here: 404 without leaking existence (design §7.5).
            raise SkillNotFoundError()
        return skill

    def _parse_upload(self, filename: str, data: bytes) -> tuple[SkillMeta, dict[str, bytes]]:
        if len(data) > MAX_BUNDLE_SIZE:
            raise SkillFileTooLargeError()
        lower = (filename or "").lower()
        if lower.endswith(_ARCHIVE_SUFFIXES):
            try:
                files = unpack_zip_bytes(data)
            except BundleTooLargeError as exc:
                raise SkillBundleTooLargeError() from exc
            except ValueError as exc:
                raise SkillValidationError(msg=str(exc))
            if sum(len(c) for c in files.values()) > MAX_UNPACKED_SIZE:
                raise SkillBundleTooLargeError()
        elif lower.endswith(".md"):
            files = {SKILL_MD: data}
        else:
            raise SkillValidationError(msg="unsupported file type: expecting .md, .zip or .skill")
        return self._extract_meta(files), files

    def _extract_meta(self, files: dict[str, bytes]) -> SkillMeta:
        """Parse SKILL.md frontmatter from a bundle into a SkillMeta.

        Shared by the upload and GitHub-import paths so both validate identically.
        A frontmatter ``name`` that violates the skill-ID spec (external skills
        commonly ship capitalized names, e.g. ``Presentations``) is normalized via
        ``slugify_pinyin`` instead of rejected: SKILL.md is rewritten in place so
        the stored frontmatter still equals the bundle directory name (deepagents
        hard constraint), and the original name becomes the display name.
        """
        if SKILL_MD not in files:
            raise SkillValidationError(msg="SKILL.md not found")
        try:
            validate_files(files)
        except ValueError as exc:
            raise SkillValidationError(msg="Invalid skill bundle") from exc
        try:
            meta, body = parse_skill_md(files[SKILL_MD].decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise SkillValidationError(msg=str(exc))
        name = str(meta.get("name") or "").strip()
        description = str(meta.get("description") or "").strip()
        metadata = meta.get("metadata") or {}
        display_name = str(metadata.get(DISPLAY_NAME_META_KEY) or "").strip() or name
        if not description:
            raise SkillValidationError(msg="frontmatter 'description' is required")
        normalized_from: str | None = None
        if name and validate_skill_name(name):
            slug = slugify_pinyin(name)
            if not validate_skill_name(slug):
                normalized_from, name = name, slug
                display_name = str(metadata.get(DISPLAY_NAME_META_KEY) or "").strip() or normalized_from
                meta["name"] = slug
                if not metadata.get(DISPLAY_NAME_META_KEY):
                    metadata[DISPLAY_NAME_META_KEY] = normalized_from
                    meta["metadata"] = metadata
                files[SKILL_MD] = render_skill_md(meta, body).encode("utf-8")
        self._validate_fields(name, display_name, description)
        return SkillMeta(name, display_name, description, normalized_from)

    def _validate_fields(self, name: str, display_name: str, description: str) -> None:
        if err := validate_skill_name(name):
            raise SkillValidationError(msg=err)
        if not display_name or len(display_name) > MAX_DISPLAY_NAME_LEN:
            raise SkillValidationError(msg=f"display_name is required and must be <= {MAX_DISPLAY_NAME_LEN} chars")
        if not description or len(description) > MAX_DESCRIPTION_LEN:
            raise SkillValidationError(msg=f"description is required and must be <= {MAX_DESCRIPTION_LEN} chars")

    async def _check_duplicate(self, name: str, display_name: str, exclude_id: int | None = None) -> None:
        existing = await self.repository.get(name)
        if existing and existing.id != exclude_id:
            raise SkillNameDuplicateError()
        existing = await self.repository.by_display_name(display_name)
        if existing and existing.id != exclude_id:
            raise SkillNameDuplicateError()

    async def _load_existing_bundle(self, tenant_id, name, skill=None):
        skill = skill or await self._get_or_404(name)
        if skill.tenant_id != tenant_id:
            raise SkillNotFoundError()
        expected = (
            self.store.object_key(tenant_id, name, skill.content_hash)
            if skill.content_hash
            else self.store.object_path(tenant_id, name)
        )
        if skill.object_path != expected:
            raise SkillValidationError(msg="Invalid skill storage reference")
        try:
            return await asyncio.to_thread(self.store.load_bundle, tenant_id, name, skill.content_hash)
        except FileNotFoundError as exc:
            raise SkillNotFoundError(msg="Skill bundle is missing") from exc
        except ValueError as exc:
            raise SkillValidationError(msg="Skill bundle integrity or format check failed") from exc

    async def _publish(self, skill, files, display_name, description):
        ref = await asyncio.to_thread(self.store.write_bundle, skill.tenant_id, skill.name, files)
        values = {
            "content_hash": ref.content_hash,
            "object_path": ref.object_key,
            "size": ref.size,
            "display_name": display_name,
            "description": description,
        }
        if not await self.repository.publish(skill, values):
            raise SkillValidationError(msg="Skill changed concurrently; reload before updating")

    async def export_bundle(self, tenant_id: int, name: str) -> tuple[bytes, str]:
        """Pack the whole skill bundle as a zip (SKILL.md at the archive root).

        The layout is the inverse of ``unpack_zip_bytes``: a top-level
        wrapper directory is not added, so an exported archive re-imports
        as the same skill without path normalization.
        """
        files = await self._load_existing_bundle(tenant_id, name)
        return await asyncio.to_thread(pack_bundle_zip, files), f"{name}.skill.zip"

    async def _create(
        self,
        tenant_id: int,
        user_id: int,
        name: str,
        display_name: str,
        description: str,
        files: dict[str, bytes],
    ) -> SkillDetail:
        self._validate_fields(name, display_name, description)
        await self._check_duplicate(name, display_name)
        ref = await asyncio.to_thread(self.store.write_bundle, tenant_id, name, files)
        skill = LinsightSkill(
            tenant_id=tenant_id,
            name=name,
            display_name=display_name,
            description=description,
            enabled=True,
            source=SKILL_SOURCE_MANUAL,
            object_path=ref.object_key,
            content_hash=ref.content_hash,
            size=ref.size,
            created_by=user_id,
        )
        skill = await self.repository.create(skill)
        try:
            # Owner tuple is best-effort: PermissionService compensates failed
            # writes via failed_tuples; the resource itself is never rolled back
            # (design §7.4 step 6; FGA model registration tracked by TF-3).
            await PermissionService.authorize(
                object_type=SKILL_OBJECT_TYPE,
                object_id=str(skill.id),
                grants=[AuthorizeGrantItem(subject_type="user", subject_id=user_id, relation="owner")],
            )
        except Exception:
            logger.exception("skill owner tuple write failed: skill_id={}", skill.id)
        return await self.get_detail(tenant_id, name)
