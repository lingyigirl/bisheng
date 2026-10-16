"""open_api 测试的公共夹具。

审计服务(call_audit_service)持有**模块级**的有界队列与消费任务,它们绑定在创建
时的事件循环上;而 pytest 每个用例一个新循环——不重置就会撞
`RuntimeError: ... is bound to a different event loop`。生产只有一个事件循环,
不存在这个问题,所以修在测试侧而不是给服务加"换循环就重建"的复杂度。
"""

import pytest

from bisheng.database.models.audit_log import AuditLogDao
from bisheng.open_api.domain.services import call_audit_service


@pytest.fixture(autouse=True)
def _reset_call_audit_state():
    call_audit_service.reset_for_test()
    yield
    call_audit_service.reset_for_test()


@pytest.fixture(autouse=True)
def audit_writes(monkeypatch):
    """把审计落库换成**内存收集**,返回收集到的行。

    审计 flush 走的是全局会话(不被用例的 sqlite 夹具拦截),不拦就会把测试数据写进
    真库(本机 dev 的 MySQL)——用例只关心"写了什么行",不关心真落库,所以统一收集。
    `test_audit.py` 用它断言行数与字段,并把它换成抛异常来验证"写失败不影响调用"。
    """
    captured: list = []

    async def _sink(cls, audit_logs):
        captured.extend(audit_logs)

    monkeypatch.setattr(AuditLogDao, "ainsert_audit_logs", classmethod(_sink))
    yield captured
