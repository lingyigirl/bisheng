from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile

from bisheng.api.v1.schemas import resp_200
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.server import UploadFileExtError
from bisheng.core.cache.utils import convert_encoding_cchardet
from bisheng.core.database import get_sync_db_session
from bisheng.core.storage.minio.minio_manager import get_minio_storage
from bisheng.evaluation.domain.models.evaluation import Evaluation
from bisheng.evaluation.domain.schemas.evaluation import EvaluationCreate
from bisheng.evaluation.domain.services.evaluation_service import EvaluationService, add_evaluation_task

router = APIRouter(prefix="/evaluation", tags=["Evaluation"], dependencies=[Depends(UserPayload.get_login_user)])


@router.get("")
def get_evaluation(
    *,
    page: int | None = Query(default=1, gt=0, description="Page"),
    limit: int | None = Query(default=10, gt=0, description="Listings Per Page"),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Get a list of assessment tasks."""
    return EvaluationService.get_evaluation(login_user, page, limit)


@router.post("")
def create_evaluation(
    *,
    file: UploadFile | None = File(default=None),
    prompt: str = Form(),
    exec_type: str = Form(),
    unique_id: str = Form(),
    version: int | str | None = Form(default=None),
    compare_version: int | str | None = Form(default=None),
    reuse_dataset_id: int | None = Form(default=None),
    background_tasks: BackgroundTasks,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Create Assessment Task.

    F053: when ``compare_version`` is given, two evaluations (one per version)
    are created sharing one dataset and one compare group id. When
    ``reuse_dataset_id`` is given, the dataset file comes from that historical
    evaluation instead of an upload. Single-version calls behave exactly as
    before.
    """
    try:
        user_id = login_user.user_id
        if not version:
            version = 0
        if file is None and reuse_dataset_id is None:
            raise HTTPException(status_code=400, detail="file or reuse_dataset_id required")
        # 2026-09-05: the judge template MUST carry the three placeholders — a
        # bare instruction (e.g. "评估答案与标准答案的一致性") reaches the judge
        # without any question/answer/ground-truth context and every score is N/A.
        for placeholder in ("{question}", "{answer}", "{ground_truth}"):
            if placeholder not in prompt:
                raise HTTPException(
                    status_code=400,
                    detail=f"prompt 缺少占位符 {placeholder}（请使用评测默认提示词模板）",
                )
        reused, group_id = EvaluationService.resolve_compare_params(
            version=version,
            compare_version=compare_version,
            reuse_dataset_id=reuse_dataset_id,
            user_id=user_id,
        )
        if file is not None:
            try:
                output_file = convert_encoding_cchardet(file.file.read(), "utf-8")
                csv_data = EvaluationService.parse_csv(file_data=output_file)
                if not csv_data or "ground_truth" not in csv_data[0]:
                    raise ValueError("invalid evaluation csv")
            except Exception:
                raise UploadFileExtError()
            finally:
                file.file.seek(0)
            file_name, file_path = EvaluationService.upload_file(file=file)
        else:
            # reused comes from resolve_compare_params as (file_path, file_name)
            file_path, file_name = reused

        versions = [version] if group_id is None else [version, compare_version]
        created = []
        with get_sync_db_session() as session:
            for v in versions:
                db_evaluation = Evaluation.model_validate(
                    EvaluationCreate(
                        unique_id=unique_id,
                        exec_type=exec_type,
                        version=v,
                        prompt=prompt,
                        user_id=user_id,
                        file_name=file_name,
                        file_path=file_path,
                        compare_group_id=group_id,
                    )
                )
                session.add(db_evaluation)
                created.append(db_evaluation)
            session.commit()
            for ev in created:
                session.refresh(ev)

        for ev in created:
            background_tasks.add_task(add_evaluation_task, evaluation_id=ev.id)
        return resp_200(created if len(created) > 1 else created[0].copy())
    finally:
        if file is not None:
            file.file.close()


@router.get("/compare/{group_id}")
def get_evaluation_compare(*, group_id: str, login_user: UserPayload = Depends(UserPayload.get_login_user)):
    """F053: dual-version compare — totals, per-question table and conclusion."""
    return EvaluationService.get_evaluation_compare(login_user, group_id)


@router.delete("/{evaluation_id}", status_code=200)
def delete_evaluation(*, evaluation_id: int, login_user: UserPayload = Depends(UserPayload.get_login_user)):
    """Delete Assessment Task (Logical Delete)."""
    return EvaluationService.delete_evaluation(evaluation_id, user_payload=login_user)


@router.get("/result/file/download")
async def get_download_url(*, file_url: str, login_user: UserPayload = Depends(UserPayload.get_login_user)):
    """Get file download address."""
    minio_client = await get_minio_storage()
    download_url = await minio_client.get_share_link(file_url)
    return resp_200(data={"url": download_url})


@router.post("/{evaluation_id}/process", status_code=200)
def process_evaluation(
    *,
    evaluation_id: int,
    background_tasks: BackgroundTasks,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Perform assessment tasks manually."""
    background_tasks.add_task(add_evaluation_task, evaluation_id=evaluation_id)
