"""Idempotent builtin deploy-template seeds."""

from bisheng.gpu_manage.domain.models.deploy_template import DeployTemplate

BUILTIN_TEMPLATES = [
    {
        "name": "Qwen3-ASR-1.7B (vLLM)",
        "image": "vllm-openai-qwen3asr:local",
        "model_path": "/data1/models/Qwen3-ASR-1.7B",
        "gpu_index": "1",
        "host_port": 8020,
        "container_port": 8000,
        "served_model_name": "Qwen3-ASR-1.7B",
        "start_args": '{"--gpu-memory-utilization": 0.9, "--max-model-len": 8192}',
        "is_builtin": True,
        "remark": "Existing qwen3-asr vLLM service preset",
    },
    {
        "name": "Qwen3-8B (vLLM)",
        "image": "vllm/vllm-openai:latest",
        "model_path": "/data1/models/Qwen3-8B",
        "gpu_index": "0",
        "host_port": 8001,
        "container_port": 8000,
        "served_model_name": "Qwen3-8B",
        "start_args": '{"--gpu-memory-utilization": 0.9, "--max-model-len": 32768}',
        "is_builtin": True,
        "remark": "Chat LLM preset",
    },
]


async def ensure_builtin_templates(existing: list[DeployTemplate]) -> list[DeployTemplate]:
    """Insert missing builtin templates; return the full template list."""
    from bisheng.gpu_manage.domain.models.deploy_template import DeployTemplateDao

    existing_names = {template.name for template in existing}
    for data in BUILTIN_TEMPLATES:
        if data["name"] in existing_names:
            continue
        await DeployTemplateDao.insert(DeployTemplate(**data))
    return await DeployTemplateDao.find_all()
