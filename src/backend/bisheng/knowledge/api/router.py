from .endpoints.answer_suggestion import router as answer_suggestion_router
from .endpoints.knowledge import router as knowledge_router
from .endpoints.qa import router as qa_router
from .endpoints.knowledge_space import router as knowledge_space_router
from .endpoints.knowledge_space_tag_library import router as knowledge_space_tag_library_router
from .endpoints.knowledge_version import router as knowledge_version_router

__all__ = ['knowledge_router', 'qa_router', 'knowledge_space_router', 'knowledge_space_tag_library_router',
           'knowledge_version_router',
           # F059: answer-bar suggestions. It lives in this module because the
           # questions half calls this module's retrieval layer; the /chat path
           # prefix is naming, not ownership.
           'answer_suggestion_router']
