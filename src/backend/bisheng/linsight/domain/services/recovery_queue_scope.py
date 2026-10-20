"""Keep recovery scans in the same queue scope as ordinary dispatch."""

from bisheng.common.services.config_service import settings


def recovery_queue_namespace():
    value = getattr(settings.linsight_conf, "queue_namespace", None)
    return value if isinstance(value, str) and value else "linsight"


def scoped_recovery_state(state, retry_after):
    namespace = recovery_queue_namespace()
    return {
        **state,
        "queue_namespace": namespace,
        "queue_retry_after": retry_after,
        # Old workers cannot interpret queue ownership. Keep their unscoped
        # scanner away; scoped workers use queue_retry_after and the real lease.
        "retry_after": retry_after if namespace == "linsight" else 253402300799,
    }


def recovery_scan_due(state, now, namespace):
    state = state or {}
    if state.get("queue_namespace", "linsight") != namespace:
        return False
    return state.get("queue_retry_after", state.get("retry_after", 0)) <= now
