import json
import os.path
import tempfile
from typing import List

from fastapi import UploadFile
from loguru import logger
from pydantic import BaseModel

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.finetune import TrainFileContentError, TrainFileNotExistError
from bisheng.common.schemas.api import PageList
from bisheng.core.storage.minio.minio_manager import get_minio_storage
from bisheng.utils import generate_uuid
from ..models.preset_train import PresetTrain, PresetTrainDao


class FinetuneFileService(BaseModel):
    """Training Tasks File management"""

    @classmethod
    async def upload_file(cls, files: List[UploadFile], is_preset: bool, user: UserPayload) -> List[PresetTrain]:
        if len(files) == 0:
            raise TrainFileNotExistError()

        # Upload training files tominio
        file_root = cls.get_upload_file_root(is_preset)
        file_list = await cls.upload_file_to_minio(files, file_root, user)
        # Store preset data in database
        if is_preset:
            logger.info(f"save preset file : {file_list}")
            file_list = await PresetTrainDao.insert_batch(file_list)
        return file_list

    @classmethod
    async def upload_preset_file(cls, name: str, preset_type: int, file_path: str, user: UserPayload) -> PresetTrain:
        # Upload training files tominio
        file_root = cls.get_upload_file_root(False)
        file_id = generate_uuid()
        file_ext = os.path.basename(file_path).split(".")[-1]
        object_name = f"{file_root}/{file_id}.{file_ext}"
        minio_client = await get_minio_storage()
        await minio_client.put_object(bucket_name=minio_client.bucket, object_name=object_name, file=file_path)
        # Store preset data in database
        file_info = PresetTrain(
            id=file_id, name=name, url=object_name, type=preset_type, user_id=user.user_id, user_name=user.user_name
        )
        await PresetTrainDao.insert_batch([file_info])
        return file_info

    @classmethod
    def get_upload_file_root(cls, is_preset: bool) -> str:
        if is_preset:
            return "finetune/train_file/preset"
        else:
            return "finetune/train_file/personal"

    @classmethod
    async def upload_file_to_minio(
        cls, files: List[UploadFile], file_root: str, user: UserPayload
    ) -> List[PresetTrain]:
        minio_client = await get_minio_storage()
        ret = []
        for file in files:
            file_id = generate_uuid()
            file_ext = os.path.basename(file.filename).split(".")[-1]
            file_info = PresetTrain(
                id=file_id,
                name=file.filename,
                url=f"{file_root}/{file_id}.{file_ext}",
                user_id=user.user_id,
                user_name=user.user_name,
            )
            await minio_client.put_object(
                bucket_name=minio_client.bucket,
                object_name=file_info.url,
                file=file.file,
                content_type=file.content_type,
                length=file.size,
            )
            ret.append(file_info)
        return ret

    @classmethod
    async def get_preset_file(cls, keyword: str = None, page_size: int = None, page_num: int = None) -> PageList:
        list_res, total_count = await PresetTrainDao.search_name(keyword, page_size, page_num)
        return PageList(list=list_res, total=total_count)

    @classmethod
    async def get_preset_file_content(cls, file_id: str, user: UserPayload) -> dict:
        """Read the preset training file content (JSON array) from MinIO."""
        file_data = await PresetTrainDao.find_one(file_id)
        if not file_data:
            raise TrainFileNotExistError()

        minio_client = await get_minio_storage()
        content_bytes = await minio_client.get_object(bucket_name=minio_client.bucket, object_name=file_data.url)
        content = content_bytes.decode("utf-8") if content_bytes else ""
        return {"id": file_data.id, "name": file_data.name, "content": content}

    @classmethod
    async def save_preset_file_content(cls, file_id: str, content: str, user: UserPayload) -> None:
        """Validate and overwrite the preset training file content.

        The content must be a JSON array of {instruction, output} objects
        (the finetune training data format).
        """
        file_data = await PresetTrainDao.find_one(file_id)
        if not file_data:
            raise TrainFileNotExistError()

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise TrainFileContentError(msg=f"content is not valid JSON: {exc}") from exc
        if (
            not isinstance(data, list)
            or not data
            or not all(isinstance(item, dict) and "instruction" in item and "output" in item for item in data)
        ):
            raise TrainFileContentError(msg="content must be a JSON array of {instruction, output} objects")

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            minio_client = await get_minio_storage()
            await minio_client.put_object(bucket_name=minio_client.bucket, object_name=file_data.url, file=tmp_path)
        finally:
            os.unlink(tmp_path)

        await PresetTrainDao.touch(file_id)

    @classmethod
    async def delete_preset_file(cls, file_id: str, user: UserPayload) -> None:
        file_data = await PresetTrainDao.find_one(file_id)
        if not file_data:
            raise TrainFileNotExistError()

        logger.info(f"delete preset train file, user: {user}; file: {file_data.model_dump()}")
        await PresetTrainDao.delete_one(file_data)
        logger.info("delete preset train file success")
        return None
