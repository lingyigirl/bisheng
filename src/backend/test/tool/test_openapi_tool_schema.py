from contextlib import asynccontextmanager
from types import SimpleNamespace

import aiohttp
import pytest
from langchain_core.tools import ToolException

from bisheng_langchain.gpts.tools.api_tools.openapi import OpenApiTools
from bisheng_langchain.utils.requests import Requests, RequestsWrapper


def api_tool(parameters):
    return OpenApiTools(
        url="https://example.test", params={"method": "get", "path": "/query", "parameters": parameters}
    )


def test_openapi_parameters_are_exposed_to_model_without_losing_nested_schema():
    nested = {
        "type": "array",
        "items": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
    }
    parameters = [
        {"name": "latitude", "required": True, "in": "query", "schema": {"type": "number"}, "description": "Latitude"},
        {"name": "items", "in": "body", "schema": nested},
        {"name": "unit", "in": "query", "schema": {"type": "string", "enum": ["C", "F"], "default": "C"}},
    ]
    tool = api_tool(parameters)
    schema = tool.parse_args_schema()
    assert schema["required"] == ["latitude"]
    assert set(schema["properties"]) == {"latitude", "items", "unit"}
    assert schema["properties"]["items"]["items"]["required"] == ["code"]
    assert schema["properties"]["unit"]["enum"] == ["C", "F"]
    assert schema["properties"]["unit"]["default"] == "C"
    schema["properties"]["items"]["items"]["properties"]["code"]["type"] = "number"
    assert nested["items"]["properties"]["code"]["type"] == "string"
    bound = OpenApiTools.get_api_tool("weather", url=tool.url, params=tool.params)
    assert bound.args_schema["properties"]["latitude"]["type"] == "number"


async def test_async_openapi_http_error_is_not_returned_as_successful_html(monkeypatch):
    observed = {}

    @asynccontextmanager
    async def request(self, method, url, **kwargs):
        observed.update(kwargs)
        if kwargs.get("raise_for_status"):
            raise aiohttp.ClientResponseError(SimpleNamespace(real_url=url + "?api_key=private-key"), (), status=404)
        yield SimpleNamespace(text=lambda: "404 HTML")

    monkeypatch.setattr(Requests, "_arequest", request)
    tool = api_tool([])
    tool.async_client = RequestsWrapper()
    with pytest.raises(ToolException) as error:
        await tool.arun()
    assert str(error.value) == "OpenAPI request failed (HTTP 404)"
    assert observed["raise_for_status"] is True
