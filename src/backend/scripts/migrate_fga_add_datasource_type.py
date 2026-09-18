#!/usr/bin/env python3
"""One-off runbook: upgrade an existing OpenFGA store to the ``db_datasource`` model.

## Background — why this is a script and not a migration

F061 adds a ``db_datasource`` resource type to the static authorization model
(``bisheng/core/openfga/authorization_model.py``). OpenFGA models are immutable
and versioned *inside the store*: a running deployment keeps checking against
whatever ``model_id`` it resolved at startup, so bumping ``MODEL_VERSION`` and
shipping new code does NOT give an existing store the new type. Something has to
write the new model once, per store.

Three candidate routes were considered and two were rejected:

1. **Alembic revision** — forbidden. ``alembic/AGENTS.md`` §5 allows DDL only;
   ``f029`` is explicitly grandfathered, with the note "never add new ones like
   them". Reaching OpenFGA from a schema migration also breaks the offline
   (``--sql``) dry-run path every migration must support.
2. **``openfga.force_write_model=true``** — unusable, not merely inelegant.
   ``FGAManager._async_initialize`` resolves ``model_id`` into a *local*
   variable and never writes it back to config, so with this flag every process
   start writes ANOTHER model into the store and they accumulate without bound.
   (The flag stays off; the script exists to do the same job once, observably.)
3. **A ``scripts/`` runbook** — chosen. This directory already carries the
   ``backfill_*_rebac_*`` family for exactly this category of out-of-band FGA
   work, and the operation is naturally "run once per environment, print what
   changed, keep the old model for rollback".

## What it does

Reads the model the deployment is actually using, checks whether its
``type_definitions`` already contain ``db_datasource``, and — only if it does
not — writes the current static model as a new model version, printing the new
``model_id`` and the config key to pin it.

Idempotent: a second run sees the type and exits without writing. Additive:
OpenFGA keeps every model ever written, so rollback is repointing
``openfga.model_id`` at the previous id.

## Run order

- **After the F061 code is deployed.** The model it writes is the one *this
  checkout* ships (``get_authorization_model()``), so running it from an older
  tree writes an older model and a later run still reports the type as missing.
  ``MODEL_VERSION`` is printed on every run — check it before applying.
- **Before the feature is used.** Until the store's model declares
  ``db_datasource``, creating a datasource fails at the owner-tuple write:
  ``DatasourceService.create_datasource`` calls
  ``OwnerService.write_owner_tuple(..., enforce_fga_success=True)``, so the FGA
  error propagates and the create is refused.
- No ordering dependency on the other ``scripts/`` runbooks.

## Usage (run from ``src/backend/``)

    # Dry-run — report only, nothing is written (this is the default)
    bash scripts/migrate_fga_add_datasource_type.sh --dry-run

    # Write the new model
    bash scripts/migrate_fga_add_datasource_type.sh --apply

The wrapper exports ``config`` and ``PYTHONPATH`` and picks the interpreter
(``.venv/bin/python``, ``.venv/Scripts/python.exe`` on Windows, then ``python``
/ ``python3``). To invoke the script directly instead, set both yourself:

    config=config.yaml PYTHONPATH=./ .venv/Scripts/python.exe scripts/migrate_fga_add_datasource_type.py --dry-run

``--dry-run`` and ``--apply`` are mutually exclusive; a bare invocation is a
dry-run. The ``.sh`` wrapper also accepts the token ``apply`` as a convenience.

## After a successful apply

Set ``openfga.model_id: <printed id>`` in ``config.yaml`` and restart, **if**
your deployment pins a model id. If ``openfga.model_id`` is left unset the
manager resolves the newest model in the store at startup, so a restart alone
picks it up. The printed id is what the service will report as its model.

## Safety

- **Dry-run is the default**; only ``--apply`` writes. Every run logs the store
  id, the model id in use and the ``MODEL_VERSION`` being considered; an apply
  also logs the new model id and the rollback target.
- **Idempotent by inspection, not by the write.** Every model write creates a
  NEW version rather than being absorbed as a duplicate, so re-running cannot be
  made safe by the write itself: the script checks the store's current model for
  ``db_datasource`` and returns without writing when it is present.
- **Additive and reversible.** No model is ever deleted or modified; rollback is
  repointing ``openfga.model_id`` at the previous id.
- **No tenant-scoped tables are read or written** — the script only talks to
  OpenFGA through its config, so ``bypass_tenant_filter()`` does not apply here.
- **One caveat, in ``FGAManager._async_initialize`` rather than in this script:**
  building the app context writes when the store does not exist (it is created)
  or exists with no model at all (the shipped model is written during client
  construction). In those states there is nothing to migrate — the store has
  just received the new model — and a bare run reports "already declares". This
  is what happens on every service start, not something this script adds.

## Exit codes

    0  success (including "already present" and dry-run)
    2  OpenFGA unreachable / not initialized in this environment
    3  OpenFGA reachable but the model read or write failed
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from loguru import logger  # noqa: E402

NEW_TYPE = "db_datasource"

EXIT_OK = 0
EXIT_UNREACHABLE = 2
EXIT_MODEL_ERROR = 3


async def run(apply: bool) -> int:
    """Check the live model and, unless already migrated, write the new one."""
    from bisheng.core.openfga.authorization_model import (
        MODEL_VERSION,
        get_authorization_model,
    )
    from bisheng.core.openfga.exceptions import FGAClientError, FGAConnectionError
    from bisheng.core.openfga.manager import aget_fga_client

    client = await aget_fga_client()
    if client is None:
        logger.error(
            "OpenFGA client is not available. Check that openfga.enabled is true "
            "in the config file the service uses (the 'config' env var), and that "
            "the OpenFGA endpoint in openfga.api_url is up."
        )
        return EXIT_UNREACHABLE

    logger.info("OpenFGA store={} model in use={}", client.store_id, client.model_id)

    try:
        current = await client.read_authorization_model(client.model_id)
    except FGAConnectionError as error:
        logger.error(
            "OpenFGA is unreachable at the configured endpoint ({}). No change made.",
            error,
        )
        return EXIT_UNREACHABLE
    except FGAClientError as error:
        logger.error("Could not read authorization model {}: {}", client.model_id, error)
        return EXIT_MODEL_ERROR

    current_types = client.model_type_names(current)
    if NEW_TYPE in current_types:
        logger.info(
            "Nothing to do: model {} already declares '{}' ({} types).",
            client.model_id,
            NEW_TYPE,
            len(current_types),
        )
        return EXIT_OK

    new_model = get_authorization_model()
    new_types = client.model_type_names(new_model)
    added = [name for name in new_types if name not in current_types]

    logger.info(
        "Model {} has {} types and lacks '{}'. The shipped model (MODEL_VERSION={}) "
        "has {} types; applying would add: {}.",
        client.model_id,
        len(current_types),
        NEW_TYPE,
        MODEL_VERSION,
        len(new_types),
        ", ".join(added) or "(none)",
    )

    if not apply:
        logger.info(
            "[dry-run] No model written. Re-run with --apply to write MODEL_VERSION={}.",
            MODEL_VERSION,
        )
        return EXIT_OK

    try:
        new_model_id = await client.write_authorization_model(new_model)
    except FGAConnectionError as error:
        logger.error(
            "OpenFGA became unreachable while writing the model ({}). No model written; the store is unchanged.",
            error,
        )
        return EXIT_UNREACHABLE
    except FGAClientError as error:
        logger.error("Failed to write the authorization model: {}", error)
        return EXIT_MODEL_ERROR

    logger.info("Wrote authorization model {} (MODEL_VERSION={}).", new_model_id, MODEL_VERSION)
    logger.info(
        "Set 'openfga.model_id: {}' in config.yaml (the file the API/Celery processes "
        "load via the 'config' env var) and restart, if your deployment pins a model id. "
        "If openfga.model_id is unset, restarting is enough: the newest model in the "
        "store is picked up automatically.",
        new_model_id,
    )
    logger.info(
        "Rollback: point openfga.model_id back at {} (models are immutable and kept).",
        client.model_id,
    )
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written and exit (this is the default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write the new authorization model to the store.",
    )
    args = parser.parse_args()

    async def _amain() -> int:
        from bisheng.common.services.config_service import settings
        from bisheng.core.context.manager import (
            close_app_context,
            initialize_app_context,
        )

        await initialize_app_context(config=settings)
        try:
            return await run(apply=args.apply)
        finally:
            await close_app_context()

    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        logger.error("Interrupted before completion; the store may be partially migrated.")
        return EXIT_MODEL_ERROR


if __name__ == "__main__":
    sys.exit(main())
