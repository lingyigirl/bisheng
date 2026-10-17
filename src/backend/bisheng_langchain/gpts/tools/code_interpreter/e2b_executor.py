import base64
import hashlib
import os
import tempfile
import uuid

from e2b.sandbox.filesystem.filesystem import EntryInfo, FileType, WriteEntry
from e2b_code_interpreter import Result, Sandbox
from loguru import logger

from bisheng_langchain.gpts.tools.code_interpreter.base_executor import (
    BaseExecutor,
    is_skipped_workspace_name,
    path_namespace_rules,
)

# F035 TC-4: copy-in/copy-out thresholds. The sandbox cannot reach MinIO, so the
# worker mediates all file transfer (design §9.3.9).
#  - SIZE_AUTOPUSH: working-set files at/under this size are auto-pushed into the
#    sandbox before each run (delta by md5). Larger files must be explicitly
#    declared in ``run(required_files=...)`` to avoid silent bulk transfer.
#  - SIZE_INLINE: copy-out products at/under this size may be inlined; larger
#    products are pointer-tracked (manifest) by the workspace backend.
SIZE_AUTOPUSH = 5 * 1024 * 1024  # 5 MB
SIZE_INLINE = 1 * 1024 * 1024  # 1 MB

_SANDBOX_ROOT = "/home/user/"


class E2bCodeExecutor(BaseExecutor):
    def __init__(
        self,
        minio: dict,
        api_key: str,
        domain: str = None,
        template: str | None = None,
        timeout: int = 900,
        file_list: list[WriteEntry] = None,
        keep_sandbox: bool = False,
        **kwargs,
    ):
        """
        timeout: Create sandbox with and keep it running for 900 seconds. The
            lifetime is refreshed on every ``run`` (``_keep_alive``) so an
            active task never loses its sandbox between calls; a sandbox that
            did expire is transparently recreated (``run``'s retry path).

        template: E2B template name/alias for the sandbox; ``None`` keeps the
            SDK default (code-interpreter-v1). The tools group config
            (``config.e2b.template``) points this at ``bisheng-linsight-v1`` —
            the preinstalled variant (libreoffice / poppler / pandoc + the
            skills' Python & npm libraries) that saves a multi-minute
            ``apt-get install`` in every docx/pptx task. See
            ``docker/e2b-template/e2b.Dockerfile``.
        """
        super().__init__(minio, **kwargs)
        self.minio = minio
        self.api_key = api_key
        self.domain = domain
        self.template = template
        self.timeout = timeout
        self.file_list = file_list
        self.keep_sandbox = keep_sandbox  # 是否保持一个sandbox, 默认只有在执行的时候才创建一个沙盒
        self.sandbox_file_cache = {}  # 缓存已经下载过的文件，避免重复下载

        # F035 TC-4: workspace integration. ``workspace_backend`` is injected by
        # the linsight worker; ``_pushed_md5`` tracks content already copied into
        # the sandbox so identical files are not re-pushed across runs.
        self.workspace_backend = kwargs.get("workspace_backend")
        self._pushed_md5: set[str] = set()
        # Workspace skills are staged lazily on the first run (see
        # ``_materialize_working_set``); once-per-sandbox, so the flag is reset
        # whenever a sandbox is (re)created.
        self._skills_staged = False
        # F074 workspace→sandbox delta sync (see ``_sync_workspace_to_sandbox``):
        # ``_ws_synced`` records the LOCAL ``(mtime_ns, size)`` last pushed per
        # sandbox-relative path; ``_sandbox_after`` records the sandbox tree as of
        # the last copy-out so a file deleted by a run is restored on the next
        # one (the workspace is the source of truth; the sandbox is disposable).
        # Both reset whenever the sandbox is (re)created.
        self._ws_synced: dict[str, tuple[int, int]] = {}
        self._sandbox_after: dict[str, float] | None = None

        if self.file_list:
            new_file_list = []
            for one in self.file_list:
                with open(one.data, "rb") as f:
                    new_file_list.append({"path": one.path, "data": f.read()})
            self.file_list = new_file_list

        self.sandbox = None
        if self.keep_sandbox:
            # Pre-warm the sandbox, but tolerate construction-time failures
            # (transient E2B unavailability / no network in tests): run() will
            # lazily re-init when needed.
            try:
                self.init_sandbox()
            except Exception:
                logger.warning("E2B sandbox pre-warm failed; will lazily init on run()")
                self.sandbox = None

    @property
    def description(self) -> str:
        return (
            "A code interpreter that can execute python code. Input should be valid python code. "
            "Write final deliverables to the RELATIVE directory `output/` (e.g. `output/report.pdf`) "
            "and intermediate files to `scratch/`. NEVER use an absolute path with a leading slash "
            "such as `/output/...` or `/scratch/...` — files written outside the current working "
            "directory are DISCARDED and will NOT be delivered to the user. "
            "The sandbox ships Python (pandas / matplotlib / python-docx / openpyxl) and Node.js, "
            "runs as root and has network access: when a tool or package is missing, INSTALL it "
            "instead of skipping — `pip install` for Python packages, `npm install` for Node "
            "packages, `apt-get update && apt-get install -y` for system tools (e.g. libreoffice, "
            "poppler-utils for pdftoppm, pandoc) — run the install as its own step, then continue "
            "with the original plan. Check `which <tool>` at most once; never hunt for it with "
            "`find /` or by reading system directories (/root, /opt, /etc, /usr, /code, "
            "site-packages); never repeat an identical call that adds no new information, and do "
            "not reinstall the same failing package. Fall back to a Python/Node equivalent, or as "
            "a last resort skip an optional step, only when installation genuinely fails. "
            + path_namespace_rules(include_skills=True)
            # Skills ARE available: _materialize_working_set stages the workspace
            # `skills/` subtree into the sandbox on the first run (the up-front
            # file_list snapshot predates skill materialisation — exactly what
            # used to make the model hunt /skills across the filesystem).
        )

    def init_sandbox(self):
        if not self.sandbox:
            self.sandbox = Sandbox(
                template=self.template,
                domain=self.domain,
                api_key=self.api_key,
                timeout=self.timeout,
            )
            # A fresh sandbox needs the workspace skills re-staged.
            self._skills_staged = False
            # …and the workspace delta re-pushed (the new sandbox only received
            # the build-time file_list, nothing written since it was built).
            self._ws_synced.clear()
            self._sandbox_after = None
            self.sandbox.files.make_dir("output")  # 确保output目录存在
            if self.file_list:
                self.sandbox.files.write(self.file_list)
                # 初始化缓存信息
                files_info = self.sandbox.files.list("./")
                for file in files_info:
                    self.sandbox_file_cache[file.path] = file

    def run(self, code: str, required_files: list[str] = None):
        """
        Execute code in the E2B Code Interpreter sandbox.

        F035 TC-4: orchestrates workspace copy-in/copy-out around execution.
        :param code: The code to execute.
        :param required_files: workspace paths the script needs that exceed
            ``SIZE_AUTOPUSH``; they are streamed into the sandbox before the run.
            Smaller working-set files are auto-pushed (delta by md5).
        :return: dict with ``results``/``new_files`` (and ``copy_in_hint`` when a
            large file was used but not declared).

        Sandbox-lifetime recovery (2026-10-15): a ``keep_sandbox`` sandbox can
        expire between calls — E2B counts ``timeout`` down from the LAST
        interaction, so a long model think-gap kills it and every later call
        fails with "The sandbox was not found ... due to sandbox timeout"
        (observed aborting long task-mode runs). The run refreshes the lifetime
        on entry (``_keep_alive``) and, if the sandbox is already gone,
        recreates it once during preparation. Once ``run_code`` starts, no
        exception may trigger automatic re-execution, including copy-out errors.
        """
        required_files = required_files or []
        if self.sandbox is None:
            self.init_sandbox()
        try:
            return self._run_once(code, required_files)
        finally:
            # keep_sandbox reuses the running sandbox across calls; otherwise tear down
            if not self.keep_sandbox:
                self.close()

    def _run_once(self, code: str, required_files: list[str]) -> dict:
        """One execution attempt against the current sandbox."""
        try:
            copy_in_hint, pre_paths = self._prepare_run(required_files)
        except Exception as exc:
            if not self._sandbox_is_gone(exc):
                raise
            # Recreate only before run_code: execution/collection failures have
            # an unknown outcome and must never cause user code to run twice.
            logger.warning("E2B sandbox gone before execution ({}); recreating once", exc)
            self._reset_sandbox()
            self.init_sandbox()
            copy_in_hint, pre_paths = self._prepare_run(required_files)

        execution = self.sandbox.run_code(code)
        results, file_list = self.parse_results(execution.results)

        # copy-out: enumerate new files vs the pre-run snapshot
        new_files = self._copy_out(pre_paths)

        result = {
            "results": results,
            "stdout": execution.logs.stdout,
            "stderr": execution.logs.stderr,
            "error": execution.error,
            "file_list": file_list,
            "new_files": new_files,
        }
        if copy_in_hint:
            result["copy_in_hint"] = copy_in_hint
        return result

    def _prepare_run(self, required_files: list[str]):
        self._keep_alive()
        self._sync_workspace_to_sandbox()
        copy_in_hint = self._copy_in(required_files)
        return copy_in_hint, self._snapshot_paths()

    # ------------------------------------------------------------------
    # Sandbox lifetime: keep-alive + recreate-on-gone
    # ------------------------------------------------------------------

    def _keep_alive(self) -> None:
        """Refresh the sandbox lifetime so long tasks don't lose it between calls.

        E2B counts the timeout down from the last sandbox interaction, so a long
        model think-gap can kill a ``keep_sandbox`` run mid-task. Best-effort —
        ``run``'s recreate path covers a sandbox that is already gone.
        """
        setter = getattr(self.sandbox, "set_timeout", None)
        if not callable(setter):
            return
        try:
            setter(self.timeout)
        except Exception as exc:  # lifetime refresh must never fail the run
            logger.debug(f"E2B set_timeout failed (ignored): {exc}")

    @staticmethod
    def _sandbox_is_gone(exc: Exception) -> bool:
        """True when the error means the sandbox no longer exists (expired/GC'd)."""
        text = f"{type(exc).__name__}: {exc}".lower()
        return (
            "sandbox was not found" in text
            or "sandbox not found" in text
            or "sandbox is not running" in text
            or ("notfound" in type(exc).__name__.lower().replace("_", "") and "sandbox" in text)
        )

    def _reset_sandbox(self) -> None:
        """Drop a dead sandbox handle plus every cache scoped to it."""
        try:
            if self.sandbox is not None:
                self.sandbox.kill()
        except Exception as exc:  # already gone — nothing to kill
            logger.debug(f"E2B sandbox kill failed on reset (ignored): {exc}")
        self.sandbox = None
        self.sandbox_file_cache.clear()
        # Everything the md5 cache claims to have pushed lived only in the dead
        # sandbox; clear it so copy-in re-stages the working set into the new one.
        self._pushed_md5.clear()
        self._skills_staged = False
        # Same for the workspace delta: the replacement sandbox needs everything
        # re-pushed, and its tree is unknown until the next copy-out.
        self._ws_synced.clear()
        self._sandbox_after = None

    # ------------------------------------------------------------------
    # F035 TC-4: workspace copy-in / copy-out
    # ------------------------------------------------------------------
    def _materialize_working_set(self) -> dict:
        """Return the workspace working set as ``{workspace_rel_path: bytes}``.

        The sandbox cannot reach MinIO, so the bound workspace backend supplies
        the files it needs. Today this carries the workspace ``skills/`` subtree:

        SKILL.md tells the model to run the selected skill's scripts with the
        code interpreter, but the tool's up-front ``file_list`` snapshot is
        taken BEFORE skills are materialised — so the bundle used to be
        genuinely absent from the sandbox and the model went hunting (2026-10-15:
        full-disk ``os.walk`` scans ballooned the context to ~94K tokens and
        ended in a no-progress repeat loop). The bound backend is the same one
        the skill copy went through, so pulling it here sees the bundle no
        matter when the copy ran. Staged once per sandbox — ``_copy_in``'s md5
        cache keeps re-pushes cheap and ``_reset_sandbox`` re-stages after a
        dead sandbox is recreated.
        """
        working_set: dict[str, bytes] = {}
        backend = self.workspace_backend
        if backend is None or self._skills_staged:
            return working_set
        try:
            listing = backend.ls("skills")
        except Exception as exc:
            logger.warning(f"skill staging into sandbox skipped (ls failed): {exc}")
            return working_set
        if getattr(listing, "error", None):
            logger.warning(f"skill staging into sandbox skipped: {listing.error}")
            return working_set

        def _field(entry, key, default=None):
            if isinstance(entry, dict):
                return entry.get(key, default)
            return getattr(entry, key, default)

        entries = [
            str(_field(entry, "path"))
            for entry in (getattr(listing, "entries", None) or [])
            if _field(entry, "path") and not _field(entry, "is_dir")
        ]
        if not entries:
            self._skills_staged = True  # no skills this run — never re-list
            return working_set
        try:
            responses = backend.download_files(entries)
        except Exception as exc:
            logger.warning(f"skill staging into sandbox skipped (download failed): {exc}")
            return working_set
        missing: list[str] = []
        for index, path in enumerate(entries):
            response = responses[index] if index < len(responses) else None
            if response is None:
                missing.append(path)
                continue
            content = getattr(response, "content", None)
            if getattr(response, "error", None) or content is None:
                missing.append(path)
                continue
            if isinstance(content, str):
                content = content.encode("utf-8")
            working_set[path.lstrip("/")] = content
        if missing:
            # Retry on the next run (md5 dedupe skips whatever already landed).
            logger.warning(f"skill staging: {len(missing)} file(s) unreadable: {missing[:5]}")
            self._skills_staged = False
        else:
            self._skills_staged = True
            logger.info(f"skill staging: staged {len(working_set)} file(s) into the sandbox")
        return working_set

    def _copy_in(self, required_files: list[str]) -> str | None:
        """Push working-set files into the sandbox. Returns a hint string when a
        large file was present but not declared in ``required_files``."""
        working_set = self._materialize_working_set()
        undeclared_large: list[str] = []
        for path, content in working_set.items():
            if not isinstance(content, bytes):
                content = content.encode("utf-8")
            md5 = hashlib.md5(content).hexdigest()
            if md5 in self._pushed_md5:
                continue  # delta push: identical content already in the sandbox
            if len(content) > SIZE_AUTOPUSH and path not in required_files:
                undeclared_large.append(path)
                continue
            self.sandbox.files.write(self._sandbox_path(path), content)
            self._pushed_md5.add(md5)
        if undeclared_large:
            return (
                "Some files exceed the auto-push size limit and were not staged. "
                f"Declare them in required_files to use them: {undeclared_large}"
            )
        return None

    # ------------------------------------------------------------------
    # F074: workspace → sandbox delta sync ("the sandbox is disposable, the
    # workspace is home"). Hermes-style semantics
    # (NousResearch/hermes-agent tools/environments/file_sync.py): diff the
    # local tree by (mtime, size), push the delta, propagate deletions, roll
    # back state on failure so the next run retries. Docker there bind-mounts
    # instead of syncing; our E2B sandbox is always remote, so every provider
    # needs this.
    # ------------------------------------------------------------------
    @staticmethod
    def _sandbox_abs(workspace_rel_path: str) -> str:
        """Absolute sandbox path for a workspace-relative path."""
        return _SANDBOX_ROOT + workspace_rel_path.lstrip("/")

    def _sync_workspace_to_sandbox(self) -> None:
        """Push local workspace changes into the sandbox before a run.

        Write-before-run is what makes ``write_file`` → run work. Before this,
        files written mid-run by the file tools lived only in the workspace
        (the object-store view) while the interpreter ran in the separate E2B
        sandbox: 2026-10-15 a "转成ppt" turn wrote ``scratch/gen.js``, every
        ``node scratch/gen.js`` / ``os.path.exists`` saw it missing, and the
        model spun on identical existence checks until the task was terminated.

        Rules (workspace is the source of truth):

        - push a file whose LOCAL ``(mtime_ns, size)`` differs from the last
          pushed state (new files always count);
        - restore a pushed file missing from the sandbox (a run deleted it) —
          detected against the post-run tree recorded by ``_copy_out``;
        - delete from the sandbox only paths WE pushed that no longer exist
          locally — sandbox-only files are never touched;
        - toolchain trees (node_modules / caches), hidden entries and files
          above ``SIZE_AUTOPUSH`` are skipped exactly like the build-time
          copy-in filter;
        - best-effort: on failure roll the tracked state back (next run
          retries the whole delta) and let the run itself proceed.
        """
        root = getattr(self, "local_sync_path", None)
        if not root or self.sandbox is None or not os.path.isdir(root):
            return

        present: set[str] = set()
        planned: dict[str, tuple[int, int]] = {}
        contents: dict[str, bytes] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and not is_skipped_workspace_name(d)]
            for name in filenames:
                if name.startswith("."):
                    continue
                host_path = os.path.join(dirpath, name)
                rel = os.path.relpath(host_path, root).replace("\\", "/")
                try:
                    stat = os.stat(host_path)
                except OSError:
                    continue
                present.add(rel)
                if stat.st_size > SIZE_AUTOPUSH:
                    continue
                key = (stat.st_mtime_ns, stat.st_size)
                needs_push = self._ws_synced.get(rel) != key
                if not needs_push and self._sandbox_after is not None:
                    # The sandbox copy vanished (a run deleted it): put it back.
                    needs_push = self._sandbox_abs(rel) not in self._sandbox_after
                if not needs_push:
                    continue
                try:
                    with open(host_path, "rb") as handle:
                        contents[rel] = handle.read()
                except OSError:
                    continue
                planned[rel] = key

        to_delete = [rel for rel in self._ws_synced if rel not in present]
        if not planned and not to_delete:
            return

        previous_state = dict(self._ws_synced)
        previous_after = self._sandbox_after
        try:
            for rel, data in contents.items():
                self.sandbox.files.write(self._sandbox_abs(rel), data)
        except Exception as exc:
            self._ws_synced = previous_state
            self._sandbox_after = previous_after
            logger.warning(f"workspace→sandbox sync failed, state rolled back: {exc}")
            return

        for rel in to_delete:
            try:
                self.sandbox.files.remove(self._sandbox_abs(rel))
            except Exception as exc:
                # Keep the entry tracked so the delete is retried next run.
                logger.debug(f"workspace sync: could not delete {rel}: {exc}")
                continue
            self._ws_synced.pop(rel, None)
        self._ws_synced.update(planned)
        logger.info(f"workspace sync: pushed {len(planned)} file(s), removed {len(to_delete)} in the sandbox")

    def _snapshot_paths(self) -> dict:
        """Sandbox ``{path: modified_time}`` before a run, for copy-out diffing."""
        return {entry.path: entry.modified_time for entry in self._walk_sandbox()}

    def _copy_out(self, pre_paths: dict) -> list[dict]:
        """Enumerate files created OR modified by the run; write them to the workspace.

        ``output/`` prefix => product (delivered); ``scratch/`` => intermediate.
        Returns ``[{path, size, is_product}]`` for each new/modified file.

        Modified files count too. This used to skip every path in the pre-run
        snapshot, so a file created in one call and MODIFIED in a later call only
        ever reached the workspace as its first version — the 2026-10-15
        worksheet task delivered a 48KB / 11-sheet workbook while the workspace
        kept the 7.9KB cover-only version written first. The workspace is what
        cross-turn seeding hands to follow-ups (and the recovery truth), so the
        next round ("转成ppt") read the stale stub. The mtime from the snapshot
        tells "new" and "changed" apart from "untouched": files staged by
        copy-in get their mtime at snapshot time, so ``<=`` still excludes them.
        """
        entries = self._walk_sandbox()
        # Record the post-run tree for the next ``_sync_workspace_to_sandbox``:
        # a pushed file missing here was deleted by the run and must be restored.
        self._sandbox_after = {entry.path: entry.modified_time for entry in entries}
        new_files: list[dict] = []
        for entry in entries:
            pre_mtime = pre_paths.get(entry.path)
            if pre_mtime is not None and entry.modified_time <= pre_mtime:
                continue  # untouched since the snapshot
            rel_path = self._to_workspace_rel(entry.path)
            content = self.sandbox.files.read(entry.path, format="bytes")
            if not isinstance(content, bytes):
                content = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            is_product = rel_path.startswith("output/")
            new_files.append({"path": rel_path, "size": len(content), "is_product": is_product})
            if self.workspace_backend is not None:
                # Large products are pointer-tracked by the backend; small ones inlined.
                self.workspace_backend.write(rel_path, content)
        return new_files

    def _walk_sandbox(self) -> list[EntryInfo]:
        """Flat list of all (non-hidden) file entries in the sandbox."""
        files: list[EntryInfo] = []
        pending = ["./"]
        visited = set()
        while pending:
            directory = pending.pop()
            if directory in visited:
                continue
            visited.add(directory)
            for entry in self.sandbox.files.list(directory):
                if entry.name.startswith(".") or is_skipped_workspace_name(entry.name):
                    continue
                if entry.type in (FileType.FILE, "file"):
                    files.append(entry)
                elif entry.type in (FileType.DIR, "dir"):
                    pending.append(entry.path)
        return files

    @staticmethod
    def _sandbox_path(workspace_rel_path: str) -> str:
        return workspace_rel_path.lstrip("/")

    @staticmethod
    def _to_workspace_rel(sandbox_path: str) -> str:
        return sandbox_path.replace(_SANDBOX_ROOT, "").lstrip("/")

    def run_code_with_one_sandbox(self, code: str) -> dict:
        """
        Execute code in the E2B Code Interpreter sandbox.
        :param code: The code to execute.
        :return: The result of the dict.
        """
        if self.sandbox is None:
            raise RuntimeError("Sandbox is destroyed.")
        execution = self.sandbox.run_code(code)
        results, file_list = self.parse_results(execution.results)
        return {
            "results": results,
            "stdout": execution.logs.stdout,
            "stderr": execution.logs.stderr,
            "error": execution.error,
            "file_list": file_list,
        }

    def parse_results(self, results: list[Result]):
        """
        Parse the results from the E2B Code Interpreter.
        :param results: The results from the code execution.
        :return: Parsed results.
        """
        parsed_results = []
        file_list = []
        for result in results:
            if result.text:
                parsed_results.append({"text": result.text})
            if result.html:
                parsed_results.append({"html": result.html})
            if result.markdown:
                parsed_results.append({"markdown": result.markdown})
            if result.svg:
                parsed_results.append({"svg": result.svg})
            if result.png:
                tmp_dir = tempfile.gettempdir()
                file_path = os.path.join(tmp_dir, uuid.uuid4().hex + ".png")
                with open(file_path, "wb") as f:
                    f.write(base64.b64decode(result.png))
                file_list.append(self.upload_minio(f"{uuid.uuid4().hex}.png", file_path))
            if result.jpeg:
                tmp_dir = tempfile.gettempdir()
                file_path = os.path.join(tmp_dir, uuid.uuid4().hex + ".jpeg")
                with open(file_path, "wb") as f:
                    f.write(base64.b64decode(result.jpeg))
                file_list.append(self.upload_minio(f"{uuid.uuid4().hex}.jpeg", file_path))
            if result.json:
                parsed_results.append({"json": result.json})
            if result.data:
                parsed_results.append({"data": result.data})
        if self.local_sync_path and os.path.exists(self.local_sync_path) and self.sandbox:
            # 将沙盒中的文件同步到本地目录
            files_info = self.sandbox.files.list("./")
            self.sync_files_to_local(files_info)

        return parsed_results, file_list

    def sync_files_to_local(self, files_info: list[EntryInfo]):
        if not files_info:
            return
        for file in files_info:
            # hidden files and toolchain trees (node_modules / caches) are never
            # task state — syncing them per run was minutes of wasted file calls
            if file.name.startswith(".") or is_skipped_workspace_name(file.name):
                continue
            # Same string-or-enum tolerance as ``_walk_sandbox``: some SDK
            # generations hand back the raw ``"file"``/``"dir"`` strings.
            if file.type in (FileType.FILE, "file"):
                cached = self.sandbox_file_cache.get(file.path)
                # Only download new or modified files — and ALWAYS refresh the
                # cache entry afterwards. Leaving it stale re-downloaded the same
                # file on every run; with the per-run workspace→sandbox sync that
                # bounced: download bumps the local mtime → next sync re-pushes →
                # the push bumps the sandbox mtime → download again, forever
                # (every workspace file, every run).
                if cached is None or cached.modified_time < file.modified_time:
                    self.download_file(file)
                self.sandbox_file_cache[file.path] = file
            elif file.type in (FileType.DIR, "dir"):
                new_files_info = self.sandbox.files.list(file.path)
                self.sync_files_to_local(new_files_info)

    def download_file(self, file_info: EntryInfo):
        relative_path = file_info.path.replace("/home/user/", "")
        local_path = os.path.join(self.local_sync_path, relative_path)
        data = self.sandbox.files.read(file_info.path, format="bytes")
        if not isinstance(data, bytes):
            data = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        # Content-equal files are NOT rewritten: the rewrite bumps the local
        # mtime, which the workspace→sandbox sync (correctly) reads as "this file
        # changed" and pushes back — an endless upload/download ping-pong.
        try:
            with open(local_path, "rb") as handle:
                if handle.read() == data:
                    return
        except OSError:
            pass
        local_dir = os.path.dirname(local_path)
        os.makedirs(local_dir, exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(data)

    def close(self):
        if self.sandbox:
            try:
                self.sandbox.kill()
            except Exception as exc:  # an expired sandbox cannot be killed
                logger.debug(f"E2B sandbox kill failed (ignored): {exc}")
            self.sandbox = None


if __name__ == "__main__":
    e2b_api_key = os.environ.get("E2B_API_KEY")
    e2b_exec = E2bCodeExecutor(api_key=e2b_api_key, keep_sandbox=True, minio={}, local_sync_path="./e2b_output")
    result = e2b_exec.run(
        code="""import requests\nimport os
os.makedirs('./downloaded_wallpapers', exist_ok=True)
# 选定一个新的可靠高清风景图片链接
direct_image_url = 'https://images.unsplash.com/photo-1524758631624-e2822e304c36?ixid=MnwxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8&ixlib=rb-1.2.1&auto=format&fit=crop&w=1050&q=80'
output_path = './downloaded_wallpapers/wallpaper_1.jpg'
try:
    response = requests.get(direct_image_url, stream=True)
    if response.status_code == 200:
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        print(f'壁纸已成功下载到 {output_path}')
    else:
        print(f'请求失败，状态码: {response.status_code}')
except Exception as e:
    print(f'发生异常: {str(e)}')"""
    )
    print(result)
    print(11111)
    e2b_exec.close()

    # boxes = Sandbox.list(api_key=e2b_api_key)
    # for box in boxes:
    #     Sandbox.kill(sandbox_id=box.sandbox_id, api_key=e2b_api_key)
