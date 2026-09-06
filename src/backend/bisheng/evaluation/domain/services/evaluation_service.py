import asyncio
import io
import json
import math
import os
from collections import defaultdict
from io import BytesIO

import pandas as pd
from fastapi import HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from loguru import logger

from bisheng.api.services.assistant_agent import AssistantAgent
from bisheng.api.v1.schema.workflow import WorkflowEventType
from bisheng.api.v1.schemas import UnifiedResponseModel, resp_200
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.core.cache import InMemoryCache
from bisheng.core.cache.redis_manager import get_redis_client_sync
from bisheng.core.storage.minio.minio_manager import get_minio_storage_sync
from bisheng.database.models.assistant import AssistantDao
from bisheng.database.models.flow import FlowDao
from bisheng.database.models.flow_version import FlowVersion, FlowVersionDao
from bisheng.evaluation.domain.models.evaluation import Evaluation, EvaluationTaskStatus, ExecType
from bisheng.evaluation.domain.repositories.evaluation_repository import EvaluationRepository
from bisheng.evaluation.domain.services.answer_correctness import compute_answer_correctness
from bisheng.llm.domain.services import LLMService
from bisheng.user.domain.models.user import UserDao
from bisheng.utils import generate_uuid
from bisheng.worker.workflow.redis_callback import RedisCallback
from bisheng.worker.workflow.tasks import continue_workflow, execute_workflow, workflow_stateful_worker
from bisheng.workflow.common.workflow import WorkflowStatus

expire = 600


def _is_nan(value) -> bool:
    """NaN-safe check for float('nan') / np.nan (`x != x` is not an option here)."""
    return isinstance(value, float) and math.isnan(value)


def format_form_values(form_values) -> str:
    """Readable one-line summary of a form-type dataset row (fills the question cell).

    ``{"企业名称": "XX公司", "报表期": ["2024Q1", "2024Q2"]}`` ->
    ``"企业名称: XX公司; 报表期: 2024Q1, 2024Q2"``. Empty entries are skipped;
    empty/missing input -> ``""``.
    """
    if not isinstance(form_values, dict) or not form_values:
        return ""
    parts = []
    for label, value in form_values.items():
        if isinstance(value, list):
            text = ", ".join(str(v) for v in value if v not in (None, ""))
        else:
            text = "" if value is None else str(value)
        if text:
            parts.append(f"{label}: {text}")
    return "; ".join(parts)


def build_result_table(result: dict, columns: list) -> tuple[list[dict], dict]:
    """Per-question rows + total row from ``compute_answer_correctness`` output.

    NaN handling (2026-09-05, evaluation NaN cleanup):
    - per-question NaN (judge parse failure) renders as ``N/A`` and is excluded
      from the aggregate — one bad row must not poison the whole average;
    - denominators that are zero already come back as 0.0 from the metric layer
      (``answer_correctness.py``), so only parse failures stay NaN here;
    - a metric with no valid rows aggregates to ``N/A``.
    """
    question = result.get("question", [])
    row_list = []
    tmp_dict = defaultdict(float)
    tmp_count = defaultdict(int)

    for index, _ in enumerate(question):
        row_data = {}
        for field, title, unit_type in columns:
            value = result.get(field)[index]
            if unit_type == 3:
                row_data[title] = "N/A" if _is_nan(value) else f"{value * 100:.2f}%"
            else:
                row_data[title] = value
            if unit_type != 1 and not _is_nan(value):
                tmp_dict[field] += value
                tmp_count[field] += 1
        row_list.append(row_data)

    total_row_data = {}
    total_dict = {}
    for field, title, unit_type in columns:
        if unit_type == 3:
            cnt = tmp_count.get(field, 0)
            value = f"{(tmp_dict[field] / cnt) * 100:.2f}%" if cnt else "N/A"
            total_dict[field] = value
        else:
            value = tmp_dict.get(field)
        total_row_data[title] = value
    row_list.append(total_row_data)
    return row_list, total_dict


class EvaluationService:
    UserCache: InMemoryCache = InMemoryCache()

    @classmethod
    def resolve_compare_params(
        cls,
        *,
        version: int | str | None,
        compare_version: int | str | None = None,
        reuse_dataset_id: int | None = None,
        user_id: int,
    ) -> tuple[tuple[str, str] | None, str | None]:
        """F053: validate create-time compare params.

        Returns ``(reused_dataset, group_id)`` where ``reused_dataset`` is the
        ``(file_path, file_name)`` of the historical evaluation to reuse (or
        None when a new file is uploaded) and ``group_id`` is the fresh compare
        group uuid (or None for a single-version evaluation).

        Raises HTTPException 400/404 on invalid combinations (spec AC-02/AC-03).
        """
        if compare_version is not None and str(compare_version) == str(version or 0):
            raise HTTPException(status_code=400, detail="compare versions must be different")
        reused: tuple[str, str] | None = None
        if reuse_dataset_id is not None:
            historical = EvaluationRepository.get_user_one_evaluation(user_id, reuse_dataset_id)
            if not historical:
                raise HTTPException(status_code=404, detail="evaluation to reuse not found")
            reused = (historical.file_path, historical.file_name)
        group_id = generate_uuid() if compare_version is not None else None
        return reused, group_id

    @classmethod
    def get_evaluation(
        cls, user: UserPayload, page: int = 1, limit: int = 20
    ) -> UnifiedResponseModel[list[Evaluation]]:
        """
        Get a list of assessment tasks
        """
        data = []
        res_evaluations, total = EvaluationRepository.get_my_evaluations(user.user_id, page, limit)

        # SkillIDVertical
        flow_ids = []
        # assistantIDVertical
        assistant_ids = []
        # VersionIDVertical
        flow_version_ids = []

        for one in res_evaluations:
            if one.exec_type in [ExecType.FLOW.value, ExecType.WORKFLOW.value]:
                flow_ids.append(one.unique_id)
                if one.version:
                    flow_version_ids.append(one.version)
            if one.exec_type == ExecType.ASSISTANT.value:
                assistant_ids.append(one.unique_id)

        flow_names = {}
        flow_versions = {}
        assistant_names = {}

        if flow_ids:
            flows = FlowDao.get_flow_by_ids(flow_ids=flow_ids)
            flow_names = {str(one.id): one.name for one in flows}

        if flow_version_ids:
            versions = FlowVersionDao.get_list_by_ids(ids=flow_version_ids)
            flow_versions = {one.id: one.name for one in versions}

        if assistant_ids:
            assistants = AssistantDao.get_assistants_by_ids(assistant_ids=assistant_ids)
            assistant_names = {str(one.id): one.name for one in assistants}

        redis_client = get_redis_client_sync()

        for one in res_evaluations:
            evaluation_item = jsonable_encoder(one)
            if one.exec_type in [ExecType.FLOW.value, ExecType.WORKFLOW.value]:
                evaluation_item["unique_name"] = flow_names.get(one.unique_id)
            if one.exec_type == ExecType.ASSISTANT.value:
                evaluation_item["unique_name"] = assistant_names.get(one.unique_id)
            if one.version:
                evaluation_item["version_name"] = flow_versions.get(one.version)
            if one.result_score:
                evaluation_item["result_score"] = (
                    json.loads(one.result_score) if isinstance(one.result_score, str) else one.result_score
                )

            # Processing Task Progress
            if one.status != EvaluationTaskStatus.running.value:
                evaluation_item["progress"] = "100%"
            elif redis_client.exists(EvaluationService.get_redis_key(one.id)):
                evaluation_item["progress"] = f"{redis_client.get(EvaluationService.get_redis_key(one.id))}%"
            else:
                evaluation_item["progress"] = "0%"

            # Make sure the error description is returned to the front-end
            evaluation_item["description"] = one.description or ""
            evaluation_item["user_name"] = cls.get_user_name(one.user_id)
            data.append(evaluation_item)

        return resp_200(data={"data": data, "total": total})

    @classmethod
    @classmethod
    def get_evaluation_compare(cls, user: UserPayload, group_id: str) -> UnifiedResponseModel:
        """F053: read one compare group and build the comparison (design 决策 4).

        Returns the two evaluations (with per-row status when one side is
        running/failed — spec AC-10) plus the computed ``comparison`` (totals +
        per-question + conclusion) when both result CSVs are readable.
        """
        rows = EvaluationRepository.get_compare_group(group_id, user.user_id)
        if len(rows) != 2:
            raise HTTPException(status_code=404, detail="compare group not found")
        if rows[0].unique_id != rows[1].unique_id:
            raise HTTPException(status_code=400, detail="compare group agents mismatch")

        minio_client = get_minio_storage_sync()
        evaluations = []
        dfs: list[pd.DataFrame | None] = []
        for ev in sorted(rows, key=lambda e: e.version or 0):
            item = {
                "id": ev.id,
                "version": ev.version,
                "status": ev.status,
                "result_score": ev.result_score,
                "description": ev.description,
            }
            df = None
            if ev.status == EvaluationTaskStatus.success.value and ev.result_file_path:
                try:
                    data = minio_client.minio_client_sync.get_object(minio_client.bucket, ev.result_file_path).read()
                    # utf-8-sig: strips the BOM written since 2026-09-06 and still
                    # reads historical BOM-less files unchanged
                    df = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig")
                except Exception as e:
                    logger.warning("compare csv read failed evaluation=%s: %s", ev.id, e)
                    item["status"] = EvaluationTaskStatus.failed.value
                    item["description"] = f"result csv unreadable: {e}"
            evaluations.append(item)
            dfs.append(df)

        comparison = _build_comparison(dfs[0], dfs[1]) if all(d is not None for d in dfs) else None
        return resp_200(data={"group_id": group_id, "evaluations": evaluations, "comparison": comparison})

    @classmethod
    def delete_evaluation(cls, evaluation_id: int, user_payload: UserPayload) -> UnifiedResponseModel:
        evaluation = EvaluationRepository.get_user_one_evaluation(user_payload.user_id, evaluation_id)
        if not evaluation:
            raise HTTPException(status_code=404, detail="Evaluation not found")

        EvaluationRepository.delete_evaluation(evaluation)
        return resp_200()

    @classmethod
    def get_user_name(cls, user_id: int):
        if not user_id:
            return "system"
        user = cls.UserCache.get(user_id)
        if user:
            return user.user_name
        user = UserDao.get_user(user_id)
        if not user:
            return f"{user_id}"
        cls.UserCache.set(user_id, user)
        return user.user_name

    @classmethod
    def upload_file(cls, file: UploadFile):
        minio_client = get_minio_storage_sync()
        file_id = generate_uuid()
        file_name = file.filename

        file_ext = os.path.basename(file.filename).split(".")[-1]
        file_path = f"evaluation/dataset/{file_id}.{file_ext}"
        minio_client.put_object_sync(
            bucket_name=minio_client.bucket, object_name=file_path, file=file.file, content_type=file.content_type
        )
        return file_name, file_path

    @classmethod
    def upload_result_file(cls, df: pd.DataFrame):
        minio_client = get_minio_storage_sync()
        file_id = generate_uuid()

        csv_buffer = io.BytesIO()
        # utf-8-sig BOM so Excel/WPS open the Chinese content correctly
        df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
        csv_buffer.seek(0)

        file_path = f"evaluation/result/{file_id}.csv"
        minio_client.put_object_sync(
            bucket_name=minio_client.bucket,
            object_name=file_path,
            file=csv_buffer.read(),
            content_type="application/csv",
        )
        return file_path

    @classmethod
    def read_csv_file(cls, file_path: str):
        minio_client = get_minio_storage_sync()
        resp = minio_client.get_object_sync(bucket_name=minio_client.bucket, object_name=file_path)
        if resp is None:
            return None
        return BytesIO(resp)

    @classmethod
    def parse_csv(cls, file_data: io.BytesIO):
        df = pd.read_csv(file_data)
        df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
        # F054: question column is optional for form-type datasets — ground_truth
        # is the only required column (spec AC-10). Layouts:
        #   question,ground_truth[,form_values]   (Q&A, unchanged)
        #   ground_truth[,form_values]            (form type)
        if "ground_truth" not in df.columns:
            raise ValueError("CSV file must have a 'ground_truth' column")
        has_question = "question" in df.columns
        if has_question and df.columns[0] != "question":
            raise ValueError("'question' must be the first column when present")
        if not has_question and df.columns[0] != "ground_truth":
            raise ValueError("'ground_truth' must be the first column when 'question' is absent")

        formatted_data = []
        for row_idx, row in enumerate(df.itertuples(index=False), start=2):  # header = row 1
            item = {
                "question": str(row.question) if has_question else "",
                "ground_truth": str(row.ground_truth),
            }
            if "form_values" in df.columns:
                raw = row.form_values
                if raw is None or (isinstance(raw, float) and math.isnan(raw)) or str(raw).strip() == "":
                    item["form_values"] = ""
                else:
                    try:
                        item["form_values"] = json.loads(str(raw))
                    except Exception as e:
                        raise ValueError(f"form_values 第 {row_idx} 行不是合法 JSON: {e}") from e
            formatted_data.append(item)
        return formatted_data

    @classmethod
    def get_redis_key(cls, evaluation_id: int):
        return f"evaluation_task_progress_{evaluation_id}"


def execute_workflow_get_answer(
    workflow_info: FlowVersion, evaluation: Evaluation, question: str, form_values=None
) -> str:
    # Initialize workflow
    unique_id = generate_uuid()
    workflow_id = evaluation.unique_id
    chat_id = ""
    user_id = evaluation.user_id
    workflow = RedisCallback(unique_id, workflow_id, chat_id, user_id)
    workflow.set_workflow_data(workflow_info.data)
    workflow.set_workflow_status(WorkflowStatus.WAITING.value)
    hash_key = generate_uuid()
    worker_node = workflow_stateful_worker.find_task_node_sync(hash_key)

    execute_workflow.apply_async([unique_id, workflow_id, chat_id, user_id], queue=worker_node)

    # Listen for execution results of workflows
    input_event = None
    for event in workflow.sync_get_response_until_break():
        input_event = event

    status_info = workflow.get_workflow_status()
    if status_info["status"] == WorkflowStatus.FAILED.value:
        raise Exception(status_info.get("reason", "workflow run failed"))
    elif status_info["status"] == WorkflowStatus.SUCCESS.value:
        raise Exception("Only Q&A type workflows are currently supported")
    elif status_info["status"] == WorkflowStatus.INPUT.value:
        if not input_event:
            raise Exception("Only Q&A type workflows are currently supported")
        input_schema = input_event.message.get("input_schema", {}) or {}
        node_id = input_event.message.get("node_id")
        if input_schema.get("tab") == "form_input":
            # F054: submit the dataset form values (label-mapped) and continue
            payload, err = build_form_payload(input_schema, form_values, get_minio_storage_sync())
            if err:
                workflow.set_workflow_stop()
                raise Exception(f"[表单提交失败] {err}")
            workflow.set_user_input({node_id: payload})
        else:
            # Only workflows entered in dialog boxes are entered by default
            workflow.set_user_input({node_id: {"user_input": question}})
        workflow.set_workflow_status(WorkflowStatus.INPUT_OVER.value)
        worker_node = workflow_stateful_worker.find_task_node_sync(hash_key)
        continue_workflow.apply_async([unique_id, workflow_id, chat_id, user_id], queue=worker_node)
        events = []
        for event in workflow.sync_get_response_until_break():
            events.append(event)
        status_info = workflow.get_workflow_status()
        if status_info["status"] == WorkflowStatus.FAILED.value:
            raise Exception(status_info.get("reason", "workflow run failed"))
        elif status_info["status"] in [WorkflowStatus.SUCCESS.value, WorkflowStatus.INPUT.value]:
            workflow.set_workflow_stop()
            # Get the content of the first output event as an answer, if not, report an error
            if not events:
                raise Exception("Only Q&A type workflows are currently supported")
            answer = None
            for event in events:
                if event.category in [
                    WorkflowEventType.OutputMsg.value,
                    WorkflowEventType.OutputWithInput.value,
                    WorkflowEventType.OutputWithChoose.value,
                ]:
                    answer = event.message.get("msg", "")
                    break
                elif event.category == WorkflowEventType.StreamMsg.value and event.type != "stream":
                    answer = event.message.get("msg", "")
                    break
            if answer is None:
                raise Exception("Only Q&A type workflows are currently supported")
            return answer
        else:
            workflow.set_workflow_stop()
            raise Exception(f"workflow status is unknown: {status_info}")
    else:
        raise Exception(f"workflow status is unknown: {status_info}")


async def add_evaluation_task(evaluation_id: int):
    evaluation = EvaluationRepository.get_one_evaluation(evaluation_id=evaluation_id)
    if not evaluation:
        return

    redis_key = EvaluationService.get_redis_key(evaluation_id)
    redis_client = get_redis_client_sync()
    try:
        file_data = EvaluationService.read_csv_file(evaluation.file_path)
        csv_data = EvaluationService.parse_csv(file_data)
        progress_increment = 80 / len(csv_data)
        current_progress = 0
        # F054: questions that failed execution (injected into the result rows)
        failed_rows: list[dict] = []

        if evaluation.exec_type == ExecType.FLOW.value:
            raise ValueError("unsupport flow")

        elif evaluation.exec_type == ExecType.ASSISTANT.value:
            assistant = await AssistantDao.aget_one_assistant(evaluation.unique_id)
            if not assistant:
                raise Exception("Assistant not found")
            gpts_agent = AssistantAgent(assistant_info=assistant, chat_id="", invoke_user_id=evaluation.user_id)
            await gpts_agent.init_assistant()
            for index, one in enumerate(csv_data):
                messages = await gpts_agent.run(one.get("question"))
                if len(messages):
                    one["answer"] = messages[-1].content
                current_progress += progress_increment
                redis_client.set(redis_key, round(current_progress))
        elif evaluation.exec_type == ExecType.WORKFLOW.value:
            workflow_info = FlowVersionDao.get_version_by_id(version_id=evaluation.version)
            if not workflow_info or workflow_info.flow_id != evaluation.unique_id:
                raise Exception("workflow version info not found")
            # F054: per-question failure isolation — one bad question must not
            # abort the whole evaluation (spec AC-07); all-failed aborts (AC-08)
            failed_rows: list[dict] = []
            valid_rows: list[dict] = []
            for index, one in enumerate(csv_data):
                try:
                    one["answer"] = await asyncio.to_thread(
                        execute_workflow_get_answer,
                        workflow_info,
                        evaluation,
                        one.get("question", ""),
                        one.get("form_values"),
                    )
                    valid_rows.append(one)
                except Exception as e:
                    reason = str(e)[:200]
                    logger.warning("evaluation={} question#{} failed: {}", evaluation_id, index + 1, reason)
                    one["answer"] = f"[执行失败] {reason}"
                    failed_rows.append(one)
            if not valid_rows:
                raise Exception(f"所有题目执行失败, 共 {len(failed_rows)} 题, 无有效答案可评分")
            csv_data = valid_rows

        _llm = await LLMService.get_evaluation_llm_object(
            evaluation.user_id,
            tenant_id=evaluation.tenant_id,
        )
        result = await asyncio.to_thread(
            compute_answer_correctness,
            _llm,
            [one.get("question") for one in csv_data],
            [one.get("answer") for one in csv_data],
            [[one.get("ground_truth")] for one in csv_data],
            evaluation.prompt,
        )
        logger.debug(f"evaluation id = {evaluation_id} result: {result}")

        columns = [
            # Data field:Title:Type(1:Text 2:Numbers 3:%)
            ("question", "question", 1),
            ("ground_truths", "ground_truth", 1),
            ("answer", "answer", 1),
            ("statements_num_gt_only", "statements_num_gt_only", 2),
            ("statements_num_answer_only", "statements_num_answer_only", 2),
            ("statements_num_overlap", "statements_num_overlap", 2),
            ("answer_recall", "recall", 3),
            ("answer_precision", "precision", 3),
            ("answer_f1", "F1", 3),
        ]
        row_list, total_dict = build_result_table(result, columns)
        # Display-only: form-type rows get their case summary in the question
        # cell so the downloaded result CSV shows which case each row belongs to.
        # The judge still scores against the original (empty) question.
        _overlay_form_question(row_list, csv_data)
        if failed_rows:
            _inject_failed_rows(row_list, failed_rows)

        df = pd.DataFrame(data=row_list, columns=[one[1] for one in columns])
        result_file_path = EvaluationService.upload_result_file(df)

        evaluation.result_score = total_dict
        evaluation.status = EvaluationTaskStatus.success.value
        evaluation.result_file_path = result_file_path
        evaluation.description = ""  # clear any stale failure text from a previous run
        EvaluationRepository.update_evaluation(evaluation=evaluation)
        redis_client.delete(redis_key)
        logger.info(f"evaluation task success id={evaluation_id}")

    except Exception as e:
        logger.exception(f"evaluation task failed id={evaluation_id} {e!s}")
        evaluation.status = EvaluationTaskStatus.failed.value
        evaluation.description = str(e)[-500:]  # Limit the length of the error description to avoid being too long
        EvaluationRepository.update_evaluation(evaluation=evaluation)
        redis_client.delete(redis_key)


# ---------------------------------------------------------------------------
# F053: dual-version compare — pure computation helpers + service entry
# ---------------------------------------------------------------------------

# (metric key, result-CSV column) in conclusion priority order
COMPARE_METRICS: list[tuple[str, str]] = [("f1", "F1"), ("recall", "recall"), ("precision", "precision")]
COMPARE_TOLERANCE = 0.01


def _parse_percent(value) -> float | None:
    """Parse a percent cell from a result CSV into a fraction.

    ``"48.49%"`` -> 0.4849; ``"N/A"`` / ``"nan%"`` / empty / NaN -> None
    (excluded from deltas, spec AC-09; historical CSVs still carry ``nan%``).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return None if math.isnan(v) else v
    text = str(value).strip()
    if not text or text.lower() in ("na", "n/a", "nan", "nan%"):
        return None
    try:
        return float(text.rstrip("%")) / 100.0
    except ValueError:
        return None


def _align_questions(q1: list[str], q2: list[str]) -> list[tuple[str | None, str | None]]:
    """Pair questions between two result CSVs (design 决策 3).

    Fast path: same question lists (same dataset) -> positional pairs. Otherwise
    fall back to text matching; questions present on only one side pair with
    None on the other (spec AC-08). Empty questions are skipped.
    """
    # non-str cells (e.g. NaN total rows) are not questions — skip them before
    # str() turns float('nan') into the literal string "nan"
    a = [str(q).strip() for q in q1 if isinstance(q, str) and q.strip()]
    b = [str(q).strip() for q in q2 if isinstance(q, str) and q.strip()]
    if len(a) == len(b) and all(x == y for x, y in zip(a, b, strict=True)):
        return [(x, x) for x in a]
    pairs: list[tuple[str | None, str | None]] = []
    b_used: set[int] = set()
    for x in a:
        idx = next((i for i, y in enumerate(b) if y == x and i not in b_used), None)
        if idx is not None:
            b_used.add(idx)
            pairs.append((x, x))
        else:
            pairs.append((x, None))
    for i, y in enumerate(b):
        if i not in b_used:
            pairs.append((None, y))
    return pairs


def _build_comparison(df1: pd.DataFrame, df2: pd.DataFrame) -> dict:
    """Totals + per-question table + conclusion from two result CSVs.

    Deltas are v2 - v1 (positive = second version better). The conclusion walks
    COMPARE_METRICS in priority order with COMPARE_TOLERANCE; all-N/A metrics
    leave totals None and the conclusion ``unable`` (design 决策 4, spec AC-11).
    """
    q1 = df1["question"].tolist() if "question" in df1.columns else []
    q2 = df2["question"].tolist() if "question" in df2.columns else []
    pairs = _align_questions(q1, q2)

    def _metric(df: pd.DataFrame, question: str | None, col: str) -> float | None:
        if question is None or col not in df.columns:
            return None
        rows = df[df["question"].astype(str) == question]
        if rows.empty:
            return None
        return _parse_percent(rows.iloc[0].get(col))

    totals = {name: {"v1": None, "v2": None, "delta": None} for name, _ in COMPARE_METRICS}
    valid: dict[str, list[tuple[float, float]]] = {name: [] for name, _ in COMPARE_METRICS}
    per_question = []
    for x, y in pairs:
        row = {"question": x if x is not None else y, "v1": {}, "v2": {}, "verdict": "same"}
        for name, col in COMPARE_METRICS:
            v1 = _metric(df1, x, col)
            v2 = _metric(df2, y, col)
            row["v1"][name] = v1
            row["v2"][name] = v2
            if v1 is not None and v2 is not None:
                valid[name].append((v1, v2))
        d1 = row["v1"].get("f1")
        d2 = row["v2"].get("f1")
        if d1 is None or d2 is None:
            row["verdict"] = "na"
        elif d2 - d1 > COMPARE_TOLERANCE:
            row["verdict"] = "better"
        elif d1 - d2 > COMPARE_TOLERANCE:
            row["verdict"] = "worse"
        per_question.append(row)

    for name, _ in COMPARE_METRICS:
        if valid[name]:
            v1 = sum(a for a, _ in valid[name]) / len(valid[name])
            v2 = sum(b for _, b in valid[name]) / len(valid[name])
            totals[name] = {"v1": v1, "v2": v2, "delta": v2 - v1}

    conclusion = "unable"
    for name, _ in COMPARE_METRICS:
        t = totals[name]
        if t["v1"] is None or t["v2"] is None:
            continue
        if t["delta"] > COMPARE_TOLERANCE:
            conclusion = "v2"
            break
        if t["delta"] < -COMPARE_TOLERANCE:
            conclusion = "v1"
            break
    return {"totals": totals, "conclusion": conclusion, "per_question": per_question}


# ---------------------------------------------------------------------------
# F054: form-input workflow evaluation — payload building + failed-row injection
# ---------------------------------------------------------------------------


def _resolve_file_value(value: str, minio_client) -> str:
    """Object path -> presigned URL (design 决策 2); full URLs pass through."""
    if "://" in value:
        return value
    if value.startswith("tmp-dir/"):
        bucket, object_name = minio_client.tmp_bucket, value[len("tmp-dir/") :]
    else:
        bucket, object_name = minio_client.bucket, value
    return minio_client.get_share_link_sync(object_name, bucket=bucket, clear_host=False, expire_days=1)


def build_form_payload(input_schema: dict, form_values, minio_client) -> tuple[dict | None, str | None]:
    """Build the form submission payload covering ALL runtime schema items.

    Maps dataset labels to the session-random runtime keys (design 决策 1) and
    converts file paths to presigned URLs. Returns ``(payload, error)`` — an
    error means this question fails with that reason (spec AC-03/AC-04/AC-05).
    """
    schema_items = (input_schema or {}).get("value") or []
    values = form_values if isinstance(form_values, dict) else {}
    label_map: dict[str, dict] = {}
    for it in schema_items:
        label = it.get("value") or it.get("label") or ""
        if label in label_map:
            return None, f"表单项 label 重复: {label}"
        label_map[label] = it
    for label in values:
        if label not in label_map:
            return None, f"表单项 label 不存在: {label}"
    payload: dict[str, object] = {}
    for it in schema_items:
        label = it.get("value") or it.get("label") or ""
        key = it["key"]
        raw = values.get(label)
        if it.get("type") == "file":
            if not raw:
                if it.get("required"):
                    return None, f"必填文件项缺值: {label}"
                payload[key] = []
                continue
            paths = raw if isinstance(raw, list) else [raw]
            payload[key] = [
                _resolve_file_value(p.strip(), minio_client) for p in paths if isinstance(p, str) and p.strip()
            ]
        else:
            payload[key] = "" if raw is None else str(raw)
    return payload, None


def _overlay_form_question(row_list: list[dict], csv_data: list[dict]) -> None:
    """F054 follow-up: fill the question cell of form-type rows with the case summary.

    Row order in ``row_list`` matches ``csv_data`` (the valid rows passed to
    ``compute_answer_correctness`` in order); the trailing total row added by
    ``build_result_table`` is excluded. Only rows whose question is empty get
    the summary — Q&A rows keep their real question.
    """
    for row, one in zip(row_list[: len(csv_data)], csv_data, strict=True):
        if not row.get("question"):
            row["question"] = format_form_values(one.get("form_values"))


def _inject_failed_rows(row_list: list[dict], failed_rows: list[dict]) -> None:
    """Append failed questions before the total row (design 决策 5).

    The total row is the last element; failed rows carry the reason in the
    ``answer`` cell and ``N/A`` metrics so the aggregate keeps excluding them
    (same N/A semantics as the 2026-09-05 NaN cleanup). Form-type failed rows
    get the case summary in the question cell (same rule as normal rows).
    """
    total_row = row_list.pop()
    for one in failed_rows:
        row_list.append(
            {
                "question": one.get("question") or format_form_values(one.get("form_values")) or "",
                "ground_truth": one.get("ground_truth", ""),
                "answer": one["answer"],
                "statements_num_gt_only": "N/A",
                "statements_num_answer_only": "N/A",
                "statements_num_overlap": "N/A",
                "recall": "N/A",
                "precision": "N/A",
                "F1": "N/A",
            }
        )
    row_list.append(total_row)
