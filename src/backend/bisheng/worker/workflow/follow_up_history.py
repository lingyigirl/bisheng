"""F067: 把持久化的会话消息还原成 `GraphState` 能吃的历史。

**为什么需要这一段。** `GraphState.history_memory` 是**进程内**对象，存活期等于那个
`Workflow` 对象；而工作流每一轮都是新建 `Workflow`（`GraphEngine` 里
`self.graph_state = GraphState()`），全仓没有任何地方把历史从持久化读回图。
所以追问轮想带上前序问答，就必须在这里补上这一段（design 决策 4）。

**输入顺序：时序（旧→新）**，与 `ChatMessageDao.aget_messages_by_chat_id`（**异步**那个）的返回一致 —— 它已经是 `DESC LIMIT N` 之后在 Python 里反转好的。**别拿同步的 `get_messages_by_chat_id` 去喂**：那个是最新在前（助手那条链自己 `.reverse()`）。两个变体顺序相反，弄错一处就是整段历史问答倒置，且不报错。

**为什么转换与读库分开。** 规则（角色按哪一列判、窗口截哪一头）错了**都不会报错**，
只表现为「历史不对」—— 所以规则做成纯函数、脱库可断言；SQL 与排序留给调用方。

**角色按 `category` 判，不按 `is_bot`。** `ChatResponse.is_bot` 默认就是 `True`，
连问句也带着它。按 `is_bot` 判会把每一条问句都认成回答 —— 历史照样有内容，
下游却看到「用户从没说过话」。`worker/workflow/redis_callback.py` 里为同一个坑
留过一行注释（"Keyed on category, not is_bot"），这里是它的第二条防线。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from bisheng.core.context.tenant import bypass_tenant_filter
from bisheng.database.models.message import ChatMessageDao

#: 只有这两类进入对话历史。`processing` / `tool_call` / `over` 等是流程事件，
#: 不是用户与系统说过的话。
HISTORY_CATEGORIES = ("question", "answer")

_SENDER_BY_CATEGORY = {"question": "human", "answer": "AI"}


def history_turns(
    rows: Iterable[Any],
    *,
    window: int,
) -> list[tuple[str, str]]:
    """`(content, msg_sender)` 列表，**按时序**（旧→新）。

    ``rows`` 必须**已经按时序**给（见模块 docstring 里两个 DAO 变体的区别）——
    这里刻意不猜输入顺序：猜错就是整段历史倒置，而且不报错。

    ``window`` 的单位是**轮**（一个 question 及其后的 answer 算一轮），保留**最新**的
    ``window`` 轮。只落了 question 的失败轮**也占一个窗口位** —— 不算它的话，窗口会随
    失败轮次悄悄变长，读回的历史比配置的多。

    非 `HISTORY_CATEGORIES` 的行直接丢弃；`window < 1` 视为不要历史。
    """
    if window < 1:
        return []

    turns: list[list[tuple[str, str]]] = []
    for row in rows:
        category = getattr(row, "category", "")
        sender = _SENDER_BY_CATEGORY.get(category)
        if sender is None:
            continue
        # 每条 question 开新的一轮；没有 question 打头的行（异常数据）也自成一
        # 轮，而不是被静默并进上一轮 —— 宁可多占一个窗口位，也不要错配。
        if category == "question" or not turns:
            turns.append([])
        turns[-1].append((row.message, sender))

    return [item for turn in turns[-window:] for item in turn]


def load_history(chat_id: str, user_id: int, window: int) -> list[tuple[str, str]]:
    """该会话此前的问答，**时序**（旧→新），最多 ``window`` 轮。

    ``window`` 是**轮**，行数按两倍取 —— 一个 question 及其后的 answer 算一轮。

    **两道 guard 必须一起发生**（design 决策 8），少哪一道都不报错、只静默出错：
    ① `bypass_tenant_filter()` —— worker 里的「环境租户」是发布时租户、缺失时回退 1，
       而 `chat_message.tenant_id` 是**用户叶子租户**，两个口径不同源。不 bypass 就是
       按错误的租户查，读到空历史：功能看着跑通，每一轮却不带上下文。
    ② 读回后按 `user_id` 过滤 —— WS 侧不校验 `chat_id` 归属，DAO 的签名里也没有 user
       维度（坑 11）。少了它，拿别人的 `chat_id` 就能把别人的会话历史灌进自己的图。
       代价是过滤发生在 `limit` **之后**（别人的 chat_id 会先读回再全丢掉，结果是空历史，
       方向安全），正常场景该 chat_id 只有自己的行，不受影响。
    """
    if window < 1:
        return []
    with bypass_tenant_filter():
        rows = ChatMessageDao.get_messages_by_chat_id(chat_id, list(HISTORY_CATEGORIES), window * 2)
    mine = [row for row in rows if row.user_id == user_id]
    # 排序在这一层做，**不靠 `reversed(rows)`**：同步 DAO 只按 `create_time DESC` 排，而
    # `create_time` 是**秒精度** —— 同一秒里写下的一问一答没有确定顺序，反过来就可能
    # 「答在问之前」（模型看到用户从没说过话）。异步变体正是为这个 bug 补了 `id` 次级键
    # （`database/models/message.py` 里那段注释写的就是它），本模块用的是没补的那个变体，
    # 所以自己按 `(create_time, id)` 复位成时序 —— 语义与异步变体一致，也不动既有 DAO
    # （它还有别的调用方）。见坑 12。
    mine.sort(key=lambda row: (row.create_time, row.id))
    return history_turns(mine, window=window)
