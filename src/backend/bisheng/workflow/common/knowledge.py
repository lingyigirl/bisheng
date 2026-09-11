from datetime import date, datetime
from typing import Any, Literal

from langchain_core.documents import Document
from loguru import logger
from pydantic import BaseModel, Field

from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum
from bisheng.common.constants.vectorstore_metadata import KNOWLEDGE_RAG_METADATA_SCHEMA
from bisheng.knowledge.domain.knowledge_rag import KnowledgeRag
from bisheng.knowledge.domain.models.knowledge import Knowledge, MetadataFieldType
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFileDao

# F055 (T-10): the document-KB retrieval builder lives in the knowledge domain so
# the hit-test endpoint and workflow nodes share ONE function (design decision 7;
# C1 — knowledge services must not depend on the workflow module).
from bisheng.knowledge.domain.services.knowledge_flow_retrieval import (
    invoke_knowledge_retriever_tool,
    retrieve_knowledge_documents,
)

# F055: pure retrieval helpers live in knowledge/rag so the knowledge-space chain
# (space_flow_retrieval) can import them without a knowledge→workflow dependency
# (C1, design 坑 3).
from bisheng.knowledge.rag.risk_compliance_filters import (
    postprocess_risk_docs,
    resolve_retriever_top_k,
)
from bisheng.llm.domain import LLMService
from bisheng.workflow.common.condition import ComparisonType
from bisheng.workflow.common.retrieval_question import normalize_retrieval_question
from bisheng.workflow.nodes.base import BaseNode


class ConditionOne(BaseModel):
    id: str = Field(..., description="Unique id for condition")
    knowledge_id: int = Field(..., description="metadata filed belong for knowledge`s id")
    metadata_field: str = Field(..., description="knowledge metadata field")
    comparison_operation: str = Field(..., description="Compare type")
    right_value_type: str = Field(..., description="Right value type")
    right_value: str = Field(..., description="Right value")

    def convert_right_value(self, field_type: str, right_value: Any, is_preset: bool) -> Any:
        # F055: dynamic "today" resolves to a concrete date for TIME fields —
        # before the preset early-return, because preset values are used raw by
        # the MySQL filter and a literal "today" would break the comparison.
        if (
            field_type == MetadataFieldType.TIME.value
            and isinstance(right_value, str)
            and right_value.strip().lower() == "today"
        ):
            right_value = datetime.combine(date.today(), datetime.min.time()).strftime("%Y-%m-%d")

        # no need to convert right value for is_empty and is_not_empty
        if self.comparison_operation in ["is_empty", "is_not_empty"] or is_preset:
            return right_value

        # only for user metadata field, need to convert right value type
        if field_type in [MetadataFieldType.STRING.value]:
            right_value = str(right_value)
            if not right_value:
                raise ValueError("Right value cannot be empty for the selected comparison operation")
        elif field_type in [MetadataFieldType.NUMBER.value]:
            right_value = int(right_value)
        elif field_type == MetadataFieldType.TIME.value:
            if isinstance(right_value, int):
                # timestamp
                right_value = datetime.fromtimestamp(right_value)
            else:
                # iso format
                right_value = datetime.fromisoformat(right_value)
            right_value = int(right_value.timestamp())
        else:
            raise ValueError(f"Unsupported metadata field type: {field_type}")
        return right_value

    def convert_preset_filed(self) -> (str, str):
        """convert preset field to mysql field in knowledge file table"""
        if self.metadata_field == "document_id":
            return "id"
        elif self.metadata_field == "document_name":
            return "file_name"
        elif self.metadata_field == "upload_time":
            return "create_time"
        elif self.metadata_field == "update_time":
            return "update_time"
        elif self.metadata_field == "uploader":
            return "user_name"
        elif self.metadata_field == "updater":
            return "updater_name"
        else:
            raise ValueError(f"Unsupported preset metadata field: {self.metadata_field}")

    def get_knowledge_file_filter(self, field_info: dict, parent_node: BaseNode, is_preset: bool) -> (str, list[dict]):
        """get knowledge file filter field info for mysql"""
        right_value = self.right_value
        if self.right_value_type == "ref" and self.right_value:
            right_value = parent_node.get_other_node_variable(self.right_value)
        field_type = field_info.get("field_type")
        right_value = self.convert_right_value(field_type, right_value, is_preset)
        if is_preset:
            field_key = self.convert_preset_filed()
            py_field: str | tuple = field_key
        else:
            # MySQL raw SQL path expression
            field_key = f"JSON_UNQUOTE(JSON_EXTRACT(`user_metadata`, '$.{self.metadata_field}.field_value'))"
            # Python-side accessor for DaMeng (used by _filter_python in KnowledgeFileDao)
            py_field = ("user_metadata", self.metadata_field)

        key_info: dict = {"py_field": py_field}
        if self.comparison_operation == ComparisonType.EQUAL:
            key_info["comparison"] = "="
            key_info["value"] = right_value
        elif self.comparison_operation == ComparisonType.NOT_EQUAL:
            key_info["comparison"] = "!="
            key_info["value"] = right_value
        elif self.comparison_operation == ComparisonType.CONTAINS:
            key_info["comparison"] = "like"
            key_info["value"] = f"%{right_value}%"
        elif self.comparison_operation == ComparisonType.NOT_CONTAINS:
            key_info["comparison"] = "not like"
            key_info["value"] = f"%{right_value}%"
        elif self.comparison_operation == ComparisonType.STARTS_WITH:
            key_info["comparison"] = "like"
            key_info["value"] = f"{right_value}%"
        elif self.comparison_operation == ComparisonType.ENDS_WITH:
            key_info["comparison"] = "like"
            key_info["value"] = f"%{right_value}"
        elif self.comparison_operation == ComparisonType.IS_EMPTY:
            key_info["comparison"] = "="
            key_info["value"] = "null"
        elif self.comparison_operation == ComparisonType.IS_NOT_EMPTY:
            key_info["comparison"] = "!="
            key_info["value"] = "null"
        elif self.comparison_operation == ComparisonType.GREATER_THAN:
            key_info["comparison"] = ">"
            key_info["value"] = right_value
        elif self.comparison_operation == ComparisonType.GREATER_THAN_OR_EQUAL:
            key_info["comparison"] = ">="
            key_info["value"] = right_value
        elif self.comparison_operation == ComparisonType.LESS_THAN:
            key_info["comparison"] = "<"
            key_info["value"] = right_value
        elif self.comparison_operation == ComparisonType.LESS_THAN_OR_EQUAL:
            key_info["comparison"] = "<="
            key_info["value"] = right_value
        else:
            raise ValueError(f"Unsupported comparison operation: {self.comparison_operation}")
        if not is_preset and self.comparison_operation in [
            ComparisonType.GREATER_THAN,
            ComparisonType.GREATER_THAN_OR_EQUAL,
            ComparisonType.LESS_THAN,
            ComparisonType.LESS_THAN_OR_EQUAL,
        ]:
            key_info["extra_filter"] = [{"comparison": "!=", "value": "null"}]
        return {field_key: key_info}


class ConditionCases(BaseModel):
    id: str = Field(default=None, description="Unique id for condition case")
    conditions: list[ConditionOne] = Field(default_factory=list, description="List of conditions")
    operator: Literal["and", "or"] = Field(default="and", description="Logical operator to combine conditions")
    enabled: bool = Field(default=False, description="Whether the condition case is enabled")

    def get_knowledge_filter(self, knowledge: Knowledge, parent_node: BaseNode) -> (str, dict):
        """if return is None, filter file is empty, don't need to retrieve from this knowledge"""
        if not self.enabled or not self.conditions:
            return "", {}

        metadata_field_info = {}
        if knowledge.metadata_fields:
            metadata_field_info = {one["field_name"]: one for one in knowledge.metadata_fields}

        # Built-in metadata fields
        preset_field_info = {
            one.field_name: one.model_dump()
            for one in KNOWLEDGE_RAG_METADATA_SCHEMA
            if one.field_name != "user_metadata"
        }
        all_filter_field = []
        for condition in self.conditions:
            if int(condition.knowledge_id) != knowledge.id:
                continue
            if field_info := preset_field_info.get(condition.metadata_field):
                filter_field_info = condition.get_knowledge_file_filter(field_info, parent_node, True)
            elif field_info := metadata_field_info.get(condition.metadata_field):
                filter_field_info = condition.get_knowledge_file_filter(field_info, parent_node, False)
            else:
                logger.warning(f"condition field {condition.metadata_field} not in knowledge metadata fields")
                raise ValueError(f"field {condition.metadata_field} not in knowledge metadata fields")
            all_filter_field.append(filter_field_info)
        if not all_filter_field:
            return "", {}

        file_ids = KnowledgeFileDao.filter_file_by_metadata_fields(knowledge.id, self.operator, all_filter_field)
        if not file_ids:
            # no file match the filter condition
            logger.debug(f"knowledge {knowledge.id} no file match the filter condition")
            return None, None
        milvus_filter = f"document_id in {file_ids}"
        es_filter = {"filter": [{"terms": {"metadata.document_id": file_ids}}]}
        return milvus_filter, es_filter


class RagUtils(BaseNode):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._knowledge_type = self.node_params.get("knowledge", {}).get("type", "knowledge")
        self._knowledge_value = [one["key"] for one in self.node_params.get("knowledge", {}).get("value", [])]

        self._advance_kwargs = self.node_params.get("advanced_retrieval_switch", {})
        self._metadata_filter = ConditionCases(**self.node_params.get("metadata_filter", {}))
        if self._advance_kwargs:
            self._advance_kwargs = self.node_params.get("advanced_retrieval_switch", {})
            self._knowledge_auth = self._advance_kwargs["user_auth"]
            self._max_chunk_size = int(self._advance_kwargs["max_chunk_size"])
            self._keyword_weight = float(self._advance_kwargs["keyword_weight"])
            self._vector_weight = float(self._advance_kwargs["vector_weight"])
            self._rerank_flag = self._advance_kwargs["rerank_flag"]
            self._rerank_model_id = self._advance_kwargs["rerank_model"]
        else:
            self._knowledge_auth = self.node_params.get("user_auth", False)
            self._max_chunk_size = int(self.node_params.get("max_chunk_size", 15000))
            self._keyword_weight = 0.5
            self._vector_weight = 0.5
            self._rerank_flag = False
            self._rerank_model_id = ""

        # F055: risk-compliance retrieval knobs — unset = legacy behaviour (AC-11/AC-13).
        # tags arrive as tag ids (ints) or {id: ...} dicts depending on the node
        # param template; both are accepted here. Bad values (hand-edited flow
        # JSON) degrade to "unset" instead of failing node init.
        tag_params = self.node_params.get("tags") or []
        self._tag_ids = []
        for one in tag_params:
            try:
                self._tag_ids.append(int(one.get("id") if isinstance(one, dict) else one))
            except (TypeError, ValueError, AttributeError):
                continue
        top_k_raw = self.node_params.get("top_k")
        try:
            self._top_k = int(top_k_raw) if top_k_raw not in (None, "") else None
        except (TypeError, ValueError):
            self._top_k = None
        threshold_raw = self.node_params.get("score_threshold")
        try:
            self._score_threshold = float(threshold_raw) if threshold_raw not in (None, "") else None
        except (TypeError, ValueError):
            self._score_threshold = None

        self._multi_milvus_retriever = None
        self._multi_es_retriever = None
        self._knowledge_vector_list = []
        self._retriever_kwargs = resolve_retriever_top_k(self._top_k)
        self._rerank_model = None
        self._knowledge_retriever_tool = None

    def _run(self, unique_id: str) -> dict[str, Any]:
        raise NotImplementedError()

    @staticmethod
    def format_timestamp(timestamp: int) -> str:
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception as e:
            logger.error(f"Error formatting timestamp {timestamp}: {e}")
            return str(timestamp)

    def retrieve_question(self, question: str) -> list[Document]:
        # F041: knowledge spaces go through the F029 view_file-filtered path.
        if self._knowledge_type == "space":
            return self._retrieve_space_question(question)

        if self._knowledge_type == "file":
            # User-uploaded temp files: no KB weight/priority/tags/effective period.
            finally_docs = invoke_knowledge_retriever_tool(
                self._multi_milvus_retriever,
                self._multi_es_retriever,
                question,
                max_content=self._max_chunk_size,
                rrf_weights=[self._vector_weight, self._keyword_weight],
                rerank=self._rerank_model,
            )
            finally_docs = postprocess_risk_docs(
                finally_docs, weight_map={}, score_threshold=self._score_threshold, top_k=self._top_k
            )
        else:
            # F055 (T-10): document-KB retrieval delegates to the shared builder —
            # the same function the hit-test endpoint uses (design decision 7).
            finally_docs = retrieve_knowledge_documents(
                knowledge_ids=self._knowledge_value,
                query=question,
                invoke_user_id=self.user_id,
                user_name=self.user_info.user_name,
                check_auth=self._knowledge_auth,
                max_content=self._max_chunk_size,
                rrf_weights=[self._vector_weight, self._keyword_weight],
                rerank=self._rerank_model,
                metadata_filter_fn=lambda kb: self._metadata_filter.get_knowledge_filter(
                    knowledge=kb, parent_node=self
                ),
                tag_ids=self._tag_ids,
                top_k=self._top_k,
                score_threshold=self._score_threshold,
                knowledge_vector_list=self._knowledge_vector_list or None,
            )
        all_file_id = {one.metadata.get("document_id") for one in finally_docs}
        file_map = {}
        if finally_docs:
            if self._knowledge_type == "knowledge":
                file_info = KnowledgeFileDao.get_file_by_ids(list(all_file_id))
                file_map = {one.id: one for one in file_info}
            for one in finally_docs:
                if "upload_time" in one.metadata:
                    one.metadata["upload_time"] = self.format_timestamp(one.metadata["upload_time"])
                if "update_time" in one.metadata:
                    one.metadata["update_time"] = self.format_timestamp(one.metadata["update_time"])
                file_id = one.metadata.get("document_id") or one.metadata.get("file_id")
                if file_id and file_map.get(file_id):
                    for user_key, user_value in one.metadata.get("user_metadata", {}).items():
                        field_info = file_map[file_id].user_metadata.get(user_key)
                        if field_info and field_info.get("field_type") == MetadataFieldType.TIME.value:
                            one.metadata["user_metadata"][user_key] = self.format_timestamp(user_value)
        return finally_docs

    def init_user_question(self) -> list[str]:
        return [
            normalize_retrieval_question(self.get_other_node_variable(one)) for one in self.node_params["user_question"]
        ]

    def init_rerank_model(self):
        if not self._rerank_flag or not self._rerank_model_id:
            return
        if self._rerank_model:
            return
        self._rerank_model = LLMService.get_bisheng_rerank_sync(
            model_id=self._rerank_model_id,
            app_id=self.workflow_id,
            app_name=self.workflow_name,
            app_type=ApplicationTypeEnum.WORKFLOW,
            user_id=self.user_id,
        )

    def init_multi_retriever(self):
        if self._knowledge_type == "knowledge":
            self.init_knowledge_retriever()
        elif self._knowledge_type == "space":
            # F041: knowledge spaces build their own retriever per-space inside
            # aretrieve_space_documents (with the F029 view_file filter), so there
            # is nothing to pre-build here.
            pass
        else:
            self.init_file_retriever()

    def _space_metadata_filter(self, space: Knowledge):
        """Per-space metadata filter (AC-08, built-in fields) → (milvus_expr, es_clauses).

        Conditions are keyed by knowledge_id, so each space resolves only its own
        conditions. Returns ``(None, None)`` when no filter is configured.
        """
        milvus_str, es_filter = self._metadata_filter.get_knowledge_filter(knowledge=space, parent_node=self)
        es_clauses = es_filter.get("filter") if es_filter else None
        return (milvus_str or None), es_clauses

    async def _aretrieve_space_docs(self, question: str) -> list[Document]:
        from bisheng.core.database import get_async_db_session
        from bisheng.knowledge.domain.repositories.implementations.knowledge_document_version_repository_impl import (
            KnowledgeDocumentVersionRepositoryImpl,
        )
        from bisheng.knowledge.domain.services.space_flow_retrieval import (
            abuild_scoped_login_user,
            aretrieve_space_documents,
        )

        # Identity: toggle ON → runtime user; OFF → config author (flow creator).
        identity_user_id = self.user_id if self._knowledge_auth else self.flow_user_id
        identity_user = await abuild_scoped_login_user(identity_user_id, self.tenant_id)
        async with get_async_db_session() as session:
            version_repo = KnowledgeDocumentVersionRepositoryImpl(session)
            return await aretrieve_space_documents(
                space_ids=self._knowledge_value,
                query=question,
                identity_user=identity_user,
                max_content=self._max_chunk_size,
                metadata_filter_fn=self._space_metadata_filter,
                rrf_weights=[self._vector_weight, self._keyword_weight],
                rerank=self._rerank_model,
                version_repo=version_repo,
                access_scope="per_user" if self._knowledge_auth else "shared",
                # F055 (design 坑 3): the space chain gets the same knobs as the
                # document-KB chain — tags/top_k/threshold are applied inside
                # aretrieve_space_documents; weight/priority there too.
                tag_ids=self._tag_ids,
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )

    def _retrieve_space_question(self, question: str) -> list[Document]:
        """F041: knowledge-space retrieval for workflow nodes (sync entry).

        Runs the async F029-filtered retrieval on the single persistent background
        loop (``run_async_safe``) — never ``asyncio.run`` — so the OpenFGA /
        aiomysql singletons are not torn down (design gotcha 5.2).
        """
        from bisheng.utils.async_utils import run_async_safe

        finally_docs = run_async_safe(self._aretrieve_space_docs(question), timeout=120)
        for one in finally_docs:
            if "upload_time" in one.metadata:
                one.metadata["upload_time"] = self.format_timestamp(one.metadata["upload_time"])
            if "update_time" in one.metadata:
                one.metadata["update_time"] = self.format_timestamp(one.metadata["update_time"])
        return finally_docs

    def init_knowledge_retriever(self):
        """F055 (T-10): document-KB retrieval is delegated to the shared builder
        (knowledge_flow_retrieval.retrieve_knowledge_documents); this only caches
        the vectorstore list across questions so the embedding/model lookups are
        not repeated per question."""
        if not self._knowledge_vector_list:
            self._knowledge_vector_list = KnowledgeRag.get_multi_knowledge_vectorstore_sync(
                invoke_user_id=self.user_id,
                knowledge_ids=self._knowledge_value,
                user_name=self.user_info.user_name,
                check_auth=self._knowledge_auth,
                include_es=self._keyword_weight > 0,
                include_milvus=self._vector_weight > 0,
            )

    def init_file_retriever(self):
        """retriever from file user upload"""
        file_ids = []
        for one in self._knowledge_value:
            file_metadata = self.get_other_node_variable(one)
            if not file_metadata:
                # No corresponding temporary file data found, User did not upload file
                continue
            file_ids.append(file_metadata[0]["document_id"])
        if not file_ids:
            self._multi_es_retriever = None
            self._multi_milvus_retriever = None
            return
        embeddings = LLMService.get_knowledge_default_embedding(self.user_id, tenant_id=self.tenant_id)
        if not embeddings:
            raise Exception("No knowledge base defaults configuredembeddingModels")

        # vectorstore use different collection_name for different embedding model
        tmp_collection_name = self.get_milvus_collection_name(embeddings.model_id)
        milvus_vector = KnowledgeRag.init_milvus_vectorstore(collection_name=tmp_collection_name, embeddings=embeddings)
        milvus_extra = {"expr": f"document_id in {file_ids}"}
        self._multi_milvus_retriever = milvus_vector.as_retriever(search_kwargs=self._retriever_kwargs | milvus_extra)

        es_extra = {"filter": [{"terms": {"metadata.document_id": file_ids}}]}
        es_vector = KnowledgeRag.init_es_vectorstore_sync(index_name=self.tmp_collection_name)
        self._multi_es_retriever = es_vector.as_retriever(search_kwargs=self._retriever_kwargs | es_extra)
