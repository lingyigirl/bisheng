from enum import Enum


class StatusEnum(str, Enum):
    SUCCESS = 'success'
    FAILED = 'failed'


# Generalized application type enumeration
class ApplicationTypeEnum(str, Enum):
    """App Type Enumeration"""

    # Workflow Apps
    WORKFLOW = "workflow"
    # Assistant App
    ASSISTANT = "assistant"
    # Inspiration App
    LINSIGHT = "linsight"
    # Daily Conversation App
    DAILY_CHAT = "daily_chat"
    # Knowledge Base Application
    KNOWLEDGE_BASE = "knowledge_base"
    KNOWLEDGE_SPACE = "knowledge_space"
    # RAGBack
    RAG_TRACEABILITY = "rag_traceability"
    # Review Apps
    EVALUATION = "evaluation"
    # Model Connectivity Testing
    MODEL_TEST = "model_test"
    # ASR
    ASR = "asr"
    # TTS
    TTS = "tts"
    # F058: document extraction (structured field extraction from a document).
    # Distinct from KNOWLEDGE_BASE so telemetry and cost attribution can tell
    # the two apart -- they use different model slots by design (decision 10).
    DOC_EXTRACT = "doc_extract"
    # F061: natural-language queries against a configured datasource (NL->SQL).
    # Distinct from DOC_EXTRACT for the same reason that one is distinct from
    # KNOWLEDGE_BASE -- it has its own model slot (`datasource_sql_llm`), so
    # telemetry and cost attribution have to be able to tell them apart.
    DATASOURCE_SQL = "datasource_sql"
    # Knowledge graph extraction (manual build). Separate type so token cost is
    # attributable to the graph job rather than to ordinary KB parsing.
    KNOWLEDGE_GRAPH = "knowledge_graph"

    UNKNOWN = "unknown"


class BaseTelemetryTypeEnum(str, Enum):
    """Basic Telemetry Event Type Enumeration"""

    # User Login Events
    USER_LOGIN = "user_login"

    # Tool Call Event
    TOOL_INVOKE = "tool_invoke"

    # Add Session Event
    NEW_MESSAGE_SESSION = "new_message_session"

    # File Parsing Event
    FILE_PARSE = "file_parse"

    # Delete Session Event
    DELETE_MESSAGE_SESSION = "delete_message_session"

    # New App Event
    NEW_APPLICATION = "new_application"

    # Edit App Event
    EDIT_APPLICATION = "edit_application"

    # Delete app event
    DELETE_APPLICATION = "delete_application"

    # Add Knowledge Base Event
    NEW_KNOWLEDGE_BASE = "new_knowledge_base"

    # Delete Knowledge Base Event
    DELETE_KNOWLEDGE_BASE = "delete_knowledge_base"

    # Knowledge Base File Upload Event
    NEW_KNOWLEDGE_FILE = "new_knowledge_file"

    # Knowledge Base File Delete Event
    DELETE_KNOWLEDGE_FILE = "delete_knowledge_file"

    # Session Message Feedback Event
    MESSAGE_FEEDBACK = "message_feedback"

    # Model Call Event
    MODEL_INVOKE = "model_invoke"

    # Number of online sessions
    APPLICATION_ALIVE = "application_alive"

    # Session Run Time
    APPLICATION_PROCESS = "application_process"
