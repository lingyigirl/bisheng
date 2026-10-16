#!/usr/bin/env python
"""F076 验收探针:把《开放API条款验收表》的 B-J 行逐条跑一遍并输出预填表.

用途(现场/预发/生产均可):
    cd src/backend
    E2E_ADMIN_PASSWORD='<管理员口令>' \
    E2E_API_BASE='http://<目标>:7860/api/v1' \
    python scripts/f076_acceptance_probe.py            # 只读探测
    ... --enable-tenant-switch                          # 允许临时打开默认租户开关(跑完还原)

它做的事:建一个一次性服务账号与若干密钥(前缀 f076-probe-),逐行执行验收动作,断言
"通过标准",最后打印一张 markdown 表(通过 / 不通过 / 需现场人工)并清理数据.

前置(不满足时对应行标"需现场"而不是失败):
- 部署总闸 open_api.enforce_keys=true(否则整表无意义,脚本直接退出);
- 默认租户开关打开(否则"不带密钥被拒"这一条达不到--见验收表说明);
- 需要真实应用/工具的行(C 的流式调用,D 的工具白名单正例)需现场用真实对接应用执行.

落库类断言(H 行):能从本机连到应用库时直查 auditlog(**须 export 与服务相同的 `config`**,
否则会读错库);连不上时打印 SQL 供人工执行.

数据面只碰自己建的一次性服务账号(前缀 f076-probe-,结束时删除),不修改业务数据.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
import uuid

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

API_BASE = os.environ.get("E2E_API_BASE", "http://localhost:7860/api/v1")
V2_BASE = API_BASE.replace("/api/v1", "/api/v2")
HOST = API_BASE.replace("/api/v1", "")
PREFIX = "f076-probe-"
ACCOUNT_NAME = f"{PREFIX}{uuid.uuid4().hex[:6]}"

PASS, FAIL, TODO = "✅ 通过", "❌ 不通过", "⤵ 需现场人工"
results: list[tuple[str, str, str]] = []  # (行, 结果, 证据)


def note(row: str, verdict: str, evidence: str) -> None:
    results.append((row, verdict, evidence[:160]))
    print(f"[{verdict}] {row} - {evidence[:120]}")


def login(client: httpx.Client, username: str, password: str) -> str:
    pk = client.get(f"{API_BASE}/user/public_key").json()["data"]["public_key"]
    public_key = serialization.load_pem_public_key(pk.encode())
    enc = base64.b64encode(public_key.encrypt(password.encode(), padding.PKCS1v15())).decode()
    body = client.post(f"{API_BASE}/user/login", json={"user_name": username, "password": enc}).json()
    assert body.get("status_code") == 200, f"login failed: {body}"
    return body["data"]["access_token"]


def expect(resp: httpx.Response, http_status: int, biz_code: int) -> bool:
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return resp.status_code == http_status and body.get("status_code") == biz_code


def bcode(resp: httpx.Response) -> tuple[int, int | None]:
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return resp.status_code, body.get("status_code")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enable-tenant-switch",
        action="store_true",
        help="允许临时打开默认租户开关(跑完还原)--只应在预发/测试环境使用",
    )
    args = parser.parse_args()

    password = os.environ.get("E2E_ADMIN_PASSWORD")
    if not password:
        print("请设置 E2E_ADMIN_PASSWORD(管理员口令,勿写入脚本)")
        return 2

    with httpx.Client(timeout=60) as client:
        if client.get(f"{API_BASE}/env").status_code != 200:
            print(f"后端不可达:{API_BASE}")
            return 2
        token = login(client, "admin", password)
        admin = {"Authorization": f"Bearer {token}"}

        settings = client.get(f"{API_BASE}/open-api/settings", headers=admin).json()["data"]
        if not settings.get("deployment_enforced"):
            print("部署总闸 open_api.enforce_keys 未开(=完全旧行为)--先开总闸并重启后端再跑本探针")
            return 2
        tenant_on = bool(settings.get("enforce_keys"))
        if not tenant_on and args.enable_tenant_switch:
            client.put(f"{API_BASE}/open-api/settings", headers=admin, json={"enforce_keys": True})
            time.sleep(5)
            tenant_on = True
            print("已临时打开默认租户开关(结束时会还原)")

        account_id = client.post(
            f"{API_BASE}/service-accounts",
            headers=admin,
            json={"name": ACCOUNT_NAME, "description": "f076 acceptance probe"},
        ).json()["data"]["id"]

        def issue(name: str, scopes: list[str], expires_at: str | None = None) -> tuple[int, str]:
            body = client.post(
                f"{API_BASE}/service-accounts/{account_id}/keys",
                headers=admin,
                json={"name": name, "scopes": scopes, "expires_at": expires_at},
            ).json()["data"]
            return body["id"], body["plaintext"]

        try:
            kb_key = issue("kb", ["knowledge:read"])[1]
            ro_key = issue("readonly", ["assistant:read"])[1]

            # ---- B: 无密钥 / 错误密钥 / 过期密钥(用非免登录端点;租户开关关时 B① 标"需现场")
            if tenant_on:
                ok = expect(client.get(f"{V2_BASE}/filelib/"), 401, 11801)
                note(
                    "B① 无密钥 → 401/11801",
                    PASS if ok else FAIL,
                    f"http/biz={bcode(client.get(f'{V2_BASE}/filelib/'))}",
                )
            else:
                note("B① 无密钥 → 401/11801", TODO, "默认租户开关未开(本次未临时打开)--旧行为放行是预期")
            bad = client.get(f"{V2_BASE}/filelib/", headers={"Authorization": "Bearer bs-sak-" + "x" * 43})
            note("B② 错误密钥 → 401/11802", PASS if expect(bad, 401, 11802) else FAIL, f"{bcode(bad)}")
            soon = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 2))
            expired_key = issue("expired", ["knowledge:read"], expires_at=soon)[1]
            time.sleep(3)
            exp = client.get(f"{V2_BASE}/filelib/", headers={"Authorization": f"Bearer {expired_key}"})
            note("B③ 过期密钥 → 401/11803", PASS if expect(exp, 401, 11803) else FAIL, f"{bcode(exp)}")

            # ---- C: 调用智能体应用(读面与停止可自动验;跑真应用的流式调用属现场)
            info = client.get(f"{V2_BASE}/assistant/list", headers={"Authorization": f"Bearer {ro_key}"})
            note("C 助手读面可调用", PASS if info.status_code == 200 else FAIL, f"http={info.status_code}")
            note("C 流式调用/工作流 invoke", TODO, "需现场用真实对接应用执行(会真实运行)")

            # ---- D: 工具三例(deny 可自动验;正例需真实应用与已绑定工具)
            listing = client.get(f"{API_BASE}/assistant", headers=admin, params={"page_size": 1}).json()
            items = (listing.get("data") or {}).get("data") or []
            if items:
                aid = str(items[0]["id"])
                client.post(
                    f"{API_BASE}/service-accounts/{account_id}/resource-grants:mutate",
                    headers=admin,
                    json={
                        "grants": [{"resource_type": "assistant", "resource_id": aid, "level": "editor"}],
                        "revokes": [],
                    },
                )
                inv_key = issue("invoke", ["assistant:invoke"])[1]
                bad_tool = client.post(
                    f"{V2_BASE}/assistant/chat/completions",
                    headers={"Authorization": f"Bearer {inv_key}"},
                    json={
                        "model": aid,
                        "messages": [{"role": "user", "content": "probe"}],
                        "stream": False,
                        "tools": [{"id": 1, "tool_key": "f076-probe-not-bound", "type": "tool"}],
                    },
                )
                code = bad_tool.json().get("status_code")
                note("D② 未绑定工具 → 403/11815", PASS if code == 11815 else FAIL, f"{bcode(bad_tool)}")
                note("D① 指定已绑定工具生效 / D③ 不传按默认", TODO, "需现场用带工具绑定的真实应用执行")
            else:
                note("D 工具三例", TODO, "环境内没有可授予的助手,需现场用真实应用执行")

            # ---- E: 未授权 vs 不存在(同码 404/11809)
            missing = client.post(
                f"{V2_BASE}/filelib/retrieve",
                headers={"Authorization": f"Bearer {kb_key}"},
                json={"knowledge_base_ids": [999_999_999], "query": "probe", "top_k": 1},
            )
            ok_missing = expect(missing, 404, 11809)
            kb_items = (
                client.get(f"{API_BASE}/knowledge", headers=admin, params={"page_size": 1})
                .json()
                .get("data", {})
                .get("data")
                or []
            )
            if kb_items:
                ungranted = client.post(
                    f"{V2_BASE}/filelib/retrieve",
                    headers={"Authorization": f"Bearer {kb_key}"},
                    json={"knowledge_base_ids": [int(kb_items[0]["id"])], "query": "probe", "top_k": 1},
                )
                same = expect(ungranted, 404, 11809) and bcode(ungranted) == bcode(missing)
                note(
                    "E 不存在/未授权同码 404/11809",
                    PASS if ok_missing and same else FAIL,
                    f"missing={bcode(missing)} ungranted={bcode(ungranted)}",
                )
            else:
                note("E① 不存在知识库 → 404/11809", PASS if ok_missing else FAIL, f"{bcode(missing)}")
                note("E② 未授权知识库 → 同码", TODO, "环境内没有知识库可做对照")

            # ---- F: 权限位(缺位 11807 + data.required;补位后即通)
            deny = client.get(
                f"{V2_BASE}/assistant/info/{uuid.uuid4().hex}", headers={"Authorization": f"Bearer {kb_key}"}
            )
            body = deny.json()
            f_ok = deny.status_code == 403 and body.get("status_code") == 11807 and body.get("data", {}).get("required")
            note("F 缺权限位 → 403/11807 + data.required", PASS if f_ok else FAIL, str(bcode(deny)))

            # ---- G: 吊销 / 停用
            g_key_id, _g_key = None, None
            g_key_id, g_key = issue("revoke", ["assistant:read"])
            g_ok_before = (
                client.get(f"{V2_BASE}/auth/whoami", headers={"Authorization": f"Bearer {g_key}"}).status_code == 200
            )
            client.post(f"{API_BASE}/service-accounts/{account_id}/keys/{g_key_id}/revoke", headers=admin)
            rev = client.get(f"{V2_BASE}/auth/whoami", headers={"Authorization": f"Bearer {g_key}"})
            note("G① 吊销后 → 401/11804", PASS if g_ok_before and expect(rev, 401, 11804) else FAIL, str(bcode(rev)))
            dis_key = issue("disable", ["assistant:read"])[1]
            client.post(f"{API_BASE}/service-accounts/{account_id}/disable", headers=admin)
            dis = client.get(f"{V2_BASE}/auth/whoami", headers={"Authorization": f"Bearer {dis_key}"})
            dis_code = dis.json().get("status_code")
            note(
                "G② 停用后 → 401(11804/11805)",
                PASS if dis.status_code == 401 and dis_code in (11804, 11805) else FAIL,
                str(bcode(dis)),
            )
            client.post(f"{API_BASE}/service-accounts/{account_id}/enable", headers=admin)

            # ---- H: 审计落库(能连库就直查,否则给 SQL)
            try:
                import asyncio

                from sqlalchemy import text

                from bisheng.core.database import get_async_db_session

                async def fetch() -> int:
                    async with get_async_db_session() as session:
                        result = await session.execute(
                            text("select count(*) from auditlog where action='open_api.call' and metadata like :m"),
                            {"m": f"%{ro_key[-4:]}%"},
                        )
                        return int(result.scalar() or 0)

                count = asyncio.run(fetch())
                note(
                    "H 调用审计落库(一次调用一条)",
                    PASS if count >= 1 else FAIL,
                    f"命中 {count} 条(尾四位 {ro_key[-4:]})",
                )
            except Exception as exc:  # 连不上库(现场在应用侧跑即可)
                note(
                    "H 调用审计落库",
                    TODO,
                    f"本机连不到应用库({type(exc).__name__});请执行:select create_time,action,target_id,metadata "
                    "from auditlog where action='open_api.call' order by create_time desc limit 5;",
                )

            # ---- I: whoami 自检
            # 用新签发的密钥: G 行"停用账号"会连带吊销此前全部密钥.
            fresh_key = issue("whoami", ["assistant:read"])[1]
            who = client.get(f"{V2_BASE}/auth/whoami", headers={"Authorization": f"Bearer {fresh_key}"})
            who_body = who.json().get("data") or {}
            i_ok = (
                who.status_code == 200 and who_body.get("key_mask", "").startswith("bs-sak-") and "scopes" in who_body
            )
            note(
                "I whoami 回显掩码/权限位",
                PASS if i_ok else FAIL,
                str({k: who_body.get(k) for k in ("key_mask", "scopes")}),
            )

            # ---- J: WebSocket 握手(有效密钥可建连 / 错误密钥被拒)
            try:
                import asyncio as _asyncio

                import websockets

                # 有效侧需要 workflow:invoke 权限位 + 一把"可编辑(editor)档及以上"的真实工作流
                # (viewer 档不满足 use_app, 连接期就会被 404/11809 拒 -- 这是刻意语义).
                wf_items = (
                    client.get(f"{API_BASE}/workflow/list", headers=admin,
                               params={"flow_type": 10, "page_size": 1, "managed": "true"})
                    .json().get("data") or {}
                ).get("data") or []
                if not wf_items:
                    note("J WS 握手", TODO, "环境内没有可授予的工作流(有效侧需真实流程), 用 wscat 人工验")
                else:
                    wf_id = str(wf_items[0]["id"])
                    client.post(f"{API_BASE}/service-accounts/{account_id}/resource-grants:mutate", headers=admin,
                                json={"grants": [{"resource_type": "workflow", "resource_id": wf_id,
                                                  "level": "editor"}], "revokes": []})
                    ws_key = issue("ws", ["workflow:invoke"])[1]
                    ws_url = HOST.replace("http", "ws") + f"/api/v2/workflow/chat/{wf_id}"

                    def _connect(headers: dict):
                        try:
                            return websockets.connect(ws_url, additional_headers=headers)
                        except TypeError:
                            return websockets.connect(ws_url, extra_headers=headers)

                    async def ws_probe() -> tuple[bool, bool]:
                        good, bad = False, False
                        try:
                            async with _connect({"Authorization": f"Bearer {ws_key}"}) as ws:
                                good = True
                                await ws.close()
                        except Exception as exc:
                            print(f"   [ws] valid-key open failed: {type(exc).__name__} {str(exc)[:90]}")
                        try:
                            async with _connect({"Authorization": "Bearer bs-sak-" + "x" * 43}):
                                bad = True
                        except Exception:
                            bad = False
                        return good, bad

                    good, bad = _asyncio.run(ws_probe())
                    note("J WS 握手(有效可连/错误被拒)", PASS if good and not bad else FAIL,
                         f"valid_open={good} bad_open={bad}")
            except ImportError:
                note("J WS 握手", TODO, "本环境无 websockets 库; 可用 wscat/浏览器控制台人工验")
        finally:
            client.delete(f"{API_BASE}/service-accounts/{account_id}", headers=admin)
            if args.enable_tenant_switch and not settings.get("enforce_keys"):
                client.put(f"{API_BASE}/open-api/settings", headers=admin, json={"enforce_keys": False})
                print("已还原默认租户开关")

    print("\n| 行 | 结果 | 证据 |")
    print("|---|---|---|")
    for row, verdict, evidence in results:
        print(f"| {row} | {verdict} | {evidence} |")
    failed = [r for r in results if r[1] == FAIL]
    print(
        f"\n合计 {len(results)} 行:通过 {sum(1 for r in results if r[1] == PASS)} / "
        f"需现场 {sum(1 for r in results if r[1] == TODO)} / 不通过 {len(failed)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
