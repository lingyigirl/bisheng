import asyncio
import base64
import contextlib
import functools
import hashlib
import io
import json
import os
import shutil
import tempfile
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Union, BinaryIO
from urllib.parse import unquote, urlparse
from uuid import uuid4

import aiofiles
import cchardet
import requests
from appdirs import user_cache_dir
from fastapi import UploadFile
from urllib3 import BaseHTTPResponse
from urllib3.util import parse_url

from bisheng.core.external.http_client.http_client_manager import get_http_client
from bisheng.core.storage.minio.minio_manager import get_minio_storage, get_minio_storage_sync

CACHE: Dict[str, Any] = {}

CACHE_DIR = user_cache_dir('bisheng', 'bisheng')


def create_cache_folder(func):
    def wrapper(*args, **kwargs):
        # Get the destination folder
        cache_path = Path(CACHE_DIR) / PREFIX

        # Create the destination folder if it doesn't exist
        os.makedirs(cache_path, exist_ok=True)

        return func(*args, **kwargs)

    return wrapper


def create_cache_folder_async(func):
    async def wrapper(*args, **kwargs):
        # Get the destination folder
        cache_path = Path(CACHE_DIR) / PREFIX

        # Create the destination folder if it doesn't exist
        os.makedirs(cache_path, exist_ok=True)

        return await func(*args, **kwargs)

    return wrapper


def memoize_dict(maxsize=128):
    cache = OrderedDict()

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            hashed = compute_dict_hash(args[0])
            key = (func.__name__, hashed, frozenset(kwargs.items()))
            if key not in cache:
                result = func(*args, **kwargs)
                cache[key] = result
                if len(cache) > maxsize:
                    cache.popitem(last=False)
            else:
                result = cache[key]
            return result

        def clear_cache():
            cache.clear()

        wrapper.clear_cache = clear_cache  # type: ignore
        wrapper.cache = cache  # type: ignore
        return wrapper

    return decorator


PREFIX = 'bisheng_cache'


@create_cache_folder
def clear_old_cache_files(max_cache_size: int = 3):
    cache_dir = Path(tempfile.gettempdir()) / PREFIX
    cache_files = list(cache_dir.glob('*.dill'))

    if len(cache_files) > max_cache_size:
        cache_files_sorted_by_mtime = sorted(cache_files,
                                             key=lambda x: x.stat().st_mtime,
                                             reverse=True)

        for cache_file in cache_files_sorted_by_mtime[max_cache_size:]:
            with contextlib.suppress(OSError):
                os.remove(cache_file)


def compute_dict_hash(graph_data):
    graph_data = filter_json(graph_data)

    cleaned_graph_json = json.dumps(graph_data, sort_keys=True)
    return hashlib.sha256(cleaned_graph_json.encode('utf-8')).hexdigest()


def filter_json(json_data):
    filtered_data = json_data.copy()

    # Remove 'viewport' and 'chatHistory' keys
    if 'viewport' in filtered_data:
        del filtered_data['viewport']
    if 'chatHistory' in filtered_data:
        del filtered_data['chatHistory']

    # Filter nodes
    if 'nodes' in filtered_data:
        for node in filtered_data['nodes']:
            if 'position' in node:
                del node['position']
            if 'positionAbsolute' in node:
                del node['positionAbsolute']
            if 'selected' in node:
                del node['selected']
            if 'dragging' in node:
                del node['dragging']

    return filtered_data


@create_cache_folder
def save_binary_file(content: str, file_name: str, accepted_types: list[str]) -> str:
    """
    Save a binary file to the specified folder.

    Args:
        content: The content of the file as a bytes object.
        file_name: The name of the file, including its extension.

    Returns:
        The path to the saved file.
    """
    if not any(file_name.endswith(suffix) for suffix in accepted_types):
        raise ValueError(f'File {file_name} is not accepted')

    # Get the destination folder
    cache_path = Path(CACHE_DIR) / PREFIX
    if not content:
        raise ValueError('Please, reload the file in the loader.')
    data = content.split(',')[1]
    decoded_bytes = base64.b64decode(data)

    # Create the full file path
    file_path = os.path.join(cache_path, file_name)

    # Save the binary content to the file
    with open(file_path, 'wb') as file:
        file.write(decoded_bytes)

    return file_path


def detect_encoding_cchardet(file_bytes: bytes, num_bytes=1024):
    """UsecchardetEncoding of the test file"""
    result = cchardet.detect(file_bytes)
    encoding = result['encoding']
    confidence = result['confidence']
    return encoding, confidence


def convert_encoding_cchardet(content: bytes, target_encoding='utf-8') -> BytesIO:
    """
    Convert file encoding to target_encoding using cchardet for detection.
    Args:
        content:
        target_encoding:

    Returns:
        BytesIO:

    """
    source_encoding, confidence = detect_encoding_cchardet(content)

    if confidence is None or confidence < 0.5 or source_encoding.lower() == target_encoding:
        return BytesIO(content)

    try:
        # decode using detected encoding
        text = content.decode(source_encoding)
    except (UnicodeDecodeError, LookupError):
        # If decoding fails, replace errors
        text = content.decode(target_encoding, errors='replace')

    # encode to target encoding
    return BytesIO(text.encode(target_encoding))


async def upload_file_to_minio(file: UploadFile, object_name, bucket_name: str) -> str:
    minio_client = await get_minio_storage()
    await minio_client.put_object(bucket_name=bucket_name, object_name=object_name, file=file.file)
    return await minio_client.get_share_link(object_name, bucket_name)


@create_cache_folder_async
async def save_file_to_folder(file: UploadFile, folder_name: str, file_name: str) -> str:
    """
    Save uploaded file tofolder_nameFolders
    :param file:
    :param folder_name:
    :param file_name:
    :return:
    """
    cache_path = Path(CACHE_DIR)
    folder_path = cache_path / folder_name

    # Create the folder if it doesn't exist
    os.makedirs(folder_path, exist_ok=True)

    # Save the file to the specified folder
    file_path = folder_path / file_name
    async with aiofiles.open(file_path, 'wb') as out_file:
        while content := await file.read(8192):
            await out_file.write(content)

    return str(file_path)


@create_cache_folder
async def save_uploaded_file(file: UploadFile, folder_name, file_name, bucket_name: str = None):
    """
    Save an uploaded file to the specified folder with a hash of its content as the file name.

    Args:
        file: The uploaded file object.
        folder_name: The name of the folder to save the file in.
        file_name: The name of the file, including its extension.
        bucket_name: The name of the bucket_name
    Returns:
        The path to the saved file.
    """

    minio_client = await get_minio_storage()
    if bucket_name is None:
        bucket_name = minio_client.tmp_bucket

    cache_path = Path(CACHE_DIR)
    folder_path = cache_path / folder_name
    # Create the folder if it doesn't exist
    if not folder_path.exists():
        folder_path.mkdir(parents=True, exist_ok=True)

    file_ext = file_name.split('.')[-1].lower()

    file_data_to_upload = None
    is_converted_text = False

    try:
        if file_ext in ('txt', 'md', 'csv'):
            raw_content = await file.read()

            file_data_to_upload = await asyncio.to_thread(
                convert_encoding_cchardet,
                content=raw_content,
                target_encoding='utf-8'
            )
            is_converted_text = True

        else:
            # For other file types, use the original uploaded file
            file_data_to_upload = file.file
            # Reset file pointer to the beginning
            file_data_to_upload.seek(0)
            is_converted_text = False

        await minio_client.put_object_tmp(object_name=file_name, file=file_data_to_upload)

    finally:
        if is_converted_text and file_data_to_upload:
            file_data_to_upload.close()

    file_path = await minio_client.get_share_link(file_name, bucket_name, clear_host=False)
    return file_path


@create_cache_folder
def save_download_file(file_input: Union[bytes, BinaryIO, BaseHTTPResponse], folder_name: str, filename: str) -> str:
    """
    Synchronous I/O intensive tasks:
    Write data stream to a temporary file
    Simultaneously calculate SHA256
    Rename a file based on the hash
    """

    # Convert to stream objects
    if isinstance(file_input, bytes):
        src_stream = BytesIO(file_input)
    else:
        src_stream = file_input
        # Make sure the pointer is at the beginning
        try:
            if hasattr(src_stream, 'seek'):
                src_stream.seek(0)
        # http response objects may not support seek, so we can ignore UnsupportedOperation exceptions
        except io.UnsupportedOperation:
            pass

    # Prepare a temporary file (write a temporary random filename first to avoid not being able to determine the filename before the hash calculation is finished).
    cache_path = Path(CACHE_DIR)
    folder_path = cache_path / folder_name

    # Create the folder if it doesn't exist
    if not folder_path.exists():
        folder_path.mkdir(exist_ok=True)

    temp_filename = f"tmp_{uuid4().hex}"
    temp_file_path = folder_path / temp_filename

    sha256_hash = hashlib.sha256()

    try:
        # Write to temporary file and calculate SHA256 simultaneously
        with open(temp_file_path, 'wb') as dst_file:
            chunk_size = 65536  # 64KB
            # minio response
            if hasattr(src_stream, 'stream'):
                for one in src_stream.stream(chunk_size):
                    sha256_hash.update(one)
                    dst_file.write(one)
            # requests response
            elif hasattr(src_stream, 'iter_content'):
                for one in src_stream.iter_content(chunk_size):
                    sha256_hash.update(one)
                    dst_file.write(one)
            # other file-like objects
            else:
                while True:
                    chunk = src_stream.read(chunk_size)
                    if not chunk:
                        break
                    sha256_hash.update(chunk)
                    dst_file.write(chunk)

        # calculate final hash
        file_hash = sha256_hash.hexdigest()

        # Logic for handling filename length limits
        safe_filename = filename
        if len(filename) > 60:
            safe_filename = filename[-60:]

        final_file_name = f'{file_hash}_{safe_filename}'
        final_file_path = folder_path / final_file_name

        # Rename (Move) Temporary File to Final Path
        # If the file already exists, decide whether to overwrite or skip it based on your needs. This example demonstrates overwriting.
        if final_file_path.exists():
            os.remove(temp_file_path)
            return str(final_file_path)

        shutil.move(str(temp_file_path), str(final_file_path))
        return str(final_file_path)

    except Exception as e:
        # Clean up temporary file in case of error
        if temp_file_path.exists():
            os.remove(temp_file_path)
        raise e
    finally:
        if isinstance(file_input, bytes):
            src_stream.close()


def file_download(file_path: str):
    """download file and return path"""

    # Try processing as a local file first (extracted URL Parameters)
    # If the system mounts a storage volume, remove ? The signature parameters behind it are read directly
    local_candidate = file_path.split("?")[0]
    if os.path.isfile(local_candidate):
        file_name = os.path.basename(local_candidate)
        # Compatible with legacy logic: handles what might be included in the filename md5 Prefix
        file_name = file_name.split("_", 1)[-1] if "_" in file_name else file_name
        return local_candidate, file_name

    minio_client = get_minio_storage_sync()
    # share link 的形态由 storage 后端自己解释(MinIO 预签名 URL / 本地相对路径)
    resolved = minio_client.resolve_share_link(file_path)

    if resolved:
        bucket_name, object_name = resolved
        filename = unquote(object_name.split("/")[-1])
        file_response = None
        try:
            file_response = minio_client.download_object_sync(bucket_name, object_name)
            file_path = save_download_file(file_response, "bisheng", filename)
        finally:
            if file_response:
                file_response.close()
                if hasattr(file_response, "release_conn"):
                    file_response.release_conn()
        return file_path, filename

    # Legacy Logic: Check if it is standard URL (Bawa http/https)
    if _is_valid_url(file_path):
        url_obj = urlparse(file_path)
        filename = unquote(url_obj.path.split("/")[-1])
        # 外部 http url: 直接请求下载, 不经过 storage
        r = requests.get(file_path, verify=False, stream=True)
        if r.status_code != 200:
            raise ValueError(f"Check the url of your file; returned status code {r.status_code}")
        # Content-Disposition header to find the filename
        content_disposition = r.headers.get("Content-Disposition")
        if content_disposition:
            filename = unquote(content_disposition).split("filename=")[-1].strip("\"'")
        try:
            file_path = save_download_file(r, "bisheng", filename)
        finally:
            r.close()
        return file_path, filename

    # Purely local file path without query parameters (normally caught above)
    if os.path.isfile(file_path):
        file_name = os.path.basename(file_path)
        file_name = file_name.split("_", 1)[-1] if "_" in file_name else file_name
        return file_path, file_name

    raise ValueError(f"File path {file_path} is not a valid file or url")


async def async_file_download(file_path: str):
    """download file and return path"""

    # Try processing as a local file first (extracted URL Parameters)
    # If the system mounts a storage volume, this will solve the problem directly
    local_candidate = file_path.split("?")[0]
    if os.path.isfile(local_candidate):
        file_name = os.path.basename(local_candidate)
        # Is it included under processing? md5 Logic of (Keep original logic)
        file_name = file_name.split("_", 1)[-1] if "_" in file_name else file_name
        return local_candidate, file_name

    minio_client = await get_minio_storage()
    # share link 的形态由 storage 后端自己解释(MinIO 预签名 URL / 本地相对路径)
    resolved = minio_client.resolve_share_link(file_path)

    if resolved:
        bucket_name, object_name = resolved
        filename = unquote(object_name.split("/")[-1])
        file_content = await minio_client.get_object(bucket_name, object_name)
        file_path = save_download_file(file_content, "bisheng", filename)
        return file_path, filename

    # Check if it is standard URL
    if _is_valid_url(file_path):
        http_client = await get_http_client()
        url_obj = parse_url(file_path)
        filename = unquote(url_obj.path.split("/")[-1])
        # 外部 http url: 直接请求下载, 不经过 storage
        r = await http_client.get(url=file_path, data_type="binary")
        if r.status_code != 200:
            raise ValueError(f"Check the url of your file; returned status code {r.status_code}")
        content_disposition = r.headers.get("Content-Disposition") if r.headers else None
        if content_disposition:
            filename = unquote(content_disposition).split("filename=")[-1].strip("\"'")
        file_content = r.body
        file_path = save_download_file(file_content, "bisheng", filename)
        return file_path, filename

    raise ValueError(f"File path {file_path} is not a valid file or url")


def _is_valid_url(url: str):
    """Check if the url is valid."""
    parsed = urlparse(url)
    return bool(parsed.netloc) and bool(parsed.scheme)
