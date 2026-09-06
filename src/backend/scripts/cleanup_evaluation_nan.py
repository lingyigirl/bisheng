#!/usr/bin/env python3
"""2026-09-05 — cleanup of evaluation scores polluted by NaN.

Root cause: per-question metrics could be ``np.nan`` (LLM judge parse failure, or
0/0 denominators) and the aggregation summed them straight into the average, so
one bad question poisoned the whole score card into ``nan%``. The code fix
(denominator-0 -> 0.0; aggregation skips NaN; all-NaN -> ``N/A``) landed in
``evaluation/domain/services/{answer_correctness,evaluation_service}.py`` — this
script repairs the historical rows.

Source rows: ``evaluation`` where ``result_score`` JSON contains ``nan%``.
For each: re-reads its result CSV from MinIO, recomputes recall/precision/F1 per
row from the statement counts (0/0 -> 0.0; judge-parse-failure rows -> ``N/A`` and
excluded from the average), rewrites the CSV and the ``result_score`` column.

Idempotent: rows without ``nan%`` are skipped, so re-running is safe.

Run from ``src/backend/`` (dry-run prints what would change; ``--apply`` writes):

    export config=config.yaml
    export PYTHONPATH="./"
    python scripts/cleanup_evaluation_nan.py            # dry-run
    python scripts/cleanup_evaluation_nan.py --apply    # write DB + MinIO CSV
"""

from __future__ import annotations

import argparse
import io
import math
import os
import sys

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import pandas as pd  # noqa: E402
from sqlmodel import select  # noqa: E402

from bisheng.core.database import get_sync_db_session  # noqa: E402
from bisheng.core.storage.minio.minio_manager import get_minio_storage_sync  # noqa: E402
from bisheng.evaluation.domain.models.evaluation import Evaluation  # noqa: E402

PERCENT_COLS = ["recall", "precision", "F1"]
COUNT_COLS = ["statements_num_gt_only", "statements_num_answer_only", "statements_num_overlap"]


def _is_nan(value) -> bool:
    if value is None:
        return True
    try:
        return isinstance(float(value), float) and math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def recompute(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute the percent columns and the total row of one result CSV."""
    out = df.copy()
    valid_flags = []
    for idx, row in out.iterrows():
        counts = [row.get(c) for c in COUNT_COLS]
        if any(_is_nan(c) for c in counts):
            # judge parse failure — no measurable statements, excluded from the average
            for col in PERCENT_COLS:
                out.at[idx, col] = "N/A"
            valid_flags.append(False)
            continue
        tp = float(counts[2] or 0)
        fp = float(counts[1] or 0)
        fn = float(counts[0] or 0)
        denom_p = tp + fp
        denom_r = tp + fn
        denom_f1 = tp + 0.5 * (fp + fn)
        precision = tp / denom_p if denom_p else 0.0
        recall = tp / denom_r if denom_r else 0.0
        f1 = tp / denom_f1 if denom_f1 else 0.0
        out.at[idx, "recall"] = _pct(recall)
        out.at[idx, "precision"] = _pct(precision)
        out.at[idx, "F1"] = _pct(f1)
        valid_flags.append(True)

    # drop the old total row (question is NaN) and append a recomputed one
    data_rows = out[~out["question"].isna()]
    data_rows = data_rows[data_rows["question"] != ""]
    totals = {}
    for col in PERCENT_COLS:
        values = data_rows[col].map(
            lambda v: float(str(v).rstrip("%")) if isinstance(v, str) and v.endswith("%") else None
        )
        values = values.dropna()
        # rows already hold percent numbers (e.g. 100.00) — average directly
        totals[col] = f"{values.mean():.2f}%" if len(values) else "N/A"
    total_row = {c: "N/A" if c not in totals else totals[c] for c in data_rows.columns}
    # keep count-sum columns NaN-free in the total row (sum of valid counts)
    for c in COUNT_COLS:
        total_row[c] = pd.to_numeric(data_rows[c], errors="coerce").sum()
    return pd.concat([data_rows, pd.DataFrame([total_row])], ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write DB and MinIO (default: dry-run)")
    args = parser.parse_args()

    minio_client = get_minio_storage_sync()
    affected: list[Evaluation] = []
    with get_sync_db_session() as session:
        rows = session.exec(select(Evaluation).where(Evaluation.result_score.is_not(None))).all()
        for ev in rows:
            score = ev.result_score
            text = score if isinstance(score, str) else str(score)
            if "nan%" in text:
                affected.append(ev)

    if not affected:
        print("no evaluation rows with nan% — nothing to clean")
        return

    print(f"affected: {len(affected)} evaluation rows")
    for ev in affected:
        print(f"\n== id={ev.id} {ev.file_name} result_file={ev.result_file_path}")
        data = minio_client.minio_client_sync.get_object(minio_client.bucket, ev.result_file_path).read()
        df = pd.read_csv(io.BytesIO(data))
        cleaned = recompute(df)
        total_row = cleaned.iloc[-1]
        new_score = {
            "answer_f1": total_row["F1"],
            "answer_recall": total_row["recall"],
            "answer_precision": total_row["precision"],
        }
        print(f"   old result_score: {ev.result_score}")
        print(f"   new result_score: {new_score}")
        print(f"   valid rows: {len(cleaned) - 1}, N/A rows: {list(cleaned['recall']).count('N/A')}")
        if args.apply:
            csv_buffer = io.BytesIO()
            cleaned.to_csv(csv_buffer, index=False)
            minio_client.put_object_sync(
                bucket_name=minio_client.bucket,
                object_name=ev.result_file_path,
                file=csv_buffer.getvalue(),
                content_type="text/csv",
            )
            # assign a NEW object to the JSON column (SQLAlchemy dirty-check trap)
            ev.result_score = new_score
            with get_sync_db_session() as session:
                from sqlmodel import update

                session.execute(
                    update(Evaluation).where(Evaluation.id == ev.id).values(result_score=new_score)
                )
                session.commit()
            print("   ✓ applied (DB + CSV rewritten)")
        else:
            print("   (dry-run — pass --apply to write)")


if __name__ == "__main__":
    main()
