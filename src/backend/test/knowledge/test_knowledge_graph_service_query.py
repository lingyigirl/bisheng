"""Regression test: graph queries must use SQLModel ``select``.

Using ``sqlalchemy.select`` with SQLModel's ``Session.exec`` returns a ``Row``
instead of the model, which broke both the status API (``job.id``) and the
worker finalizer (``job.status``).
"""

from bisheng.knowledge.domain.models.knowledge_graph import KnowledgeGraphJob
from bisheng.knowledge.domain.services import knowledge_graph_service as mod


def test_job_select_returns_model_and_job_info(db_session):
    job = KnowledgeGraphJob(
        tenant_id=1,
        knowledge_id=1,
        status=0,
        model_id=1,
        entity_types=["人物"],
    )
    db_session.add(job)
    db_session.commit()

    found = db_session.exec(
        mod.select(KnowledgeGraphJob).where(KnowledgeGraphJob.id == job.id)
    ).first()

    assert isinstance(found, KnowledgeGraphJob)
    info = mod.KnowledgeGraphService._job_info(found)
    assert info.job_id == job.id
    assert info.status == 0
    assert info.total_files == 0
