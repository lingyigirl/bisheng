"""Discard incomplete presentation state while retaining durable tool evidence."""


async def reset_incomplete_projection(manager):
    for task in await manager.get_execution_tasks():
        history = task.history or []
        completed = {step.get("call_id") for step in history if step.get("status") == "end" and step.get("call_id")}
        kept = [
            step
            for step in history
            if (step.get("step_type") == "call_user_input" and step.get("is_completed"))
            or (step.get("step_type") != "thinking" and step.get("call_id") in completed)
        ]
        await manager.update_execution_task_status(task.id, status=task.status, history=kept)
