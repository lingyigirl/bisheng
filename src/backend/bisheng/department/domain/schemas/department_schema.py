"""Pydantic DTOs for Department API requests and responses.

Part of F002-department-tree.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DepartmentMemberAffiliateRolesPayload(BaseModel):
    """兼职部门行：按部门外部 id 提交角色 id 列表。"""

    dept_id: str = Field(..., min_length=1)
    role_ids: list[int] = Field(default_factory=list)


class DepartmentMemberEditApply(BaseModel):
    """组织与成员 · 编辑人员（PRD 3.2.2）提交体。"""

    user_name: str | None = Field(None, max_length=128)
    primary_department_id: int | None = Field(
        None,
        description="主部门 Department.id；仅本地主属可改，须在操作者管辖组织子树内",
    )
    group_ids: list[int] | None = None
    # 兼职人员：仅当前上下文部门角色
    context_role_ids: list[int] | None = None
    # 主属（本地/第三方）：主部门角色 + 各附属部门角色
    primary_role_ids: list[int] | None = None
    affiliate_roles: list[DepartmentMemberAffiliateRolesPayload] | None = None


class DepartmentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    parent_id: int
    sort_order: int = 0
    default_role_ids: list[int] | None = None
    admin_user_ids: list[int] | None = None
    #: F062 节点标记：True=部门（对上级透明），False=机构（不透明，可见性隔离边界）。
    #: **不从父节点继承**（AC-03）——默认为 False（机构），与存量默认值同一套语义：
    #: 「不透明」是安全缺省，全树为机构时新规则恒等于 F057 的既有行为。
    is_department: bool = False


class DepartmentUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=50)
    sort_order: int | None = None
    default_role_ids: list[int] | None = None
    #: 与名称、默认角色一并提交时全量替换部门管理员（OpenFGA）；不传则不改管理员
    admin_user_ids: list[int] | None = None
    #: 为 True 时，将当前请求中的默认角色（若未传 ``default_role_ids`` 则用库中已有值）授予本部门全部现存成员
    apply_default_roles_to_existing_members: bool = False
    #: F062 节点标记。``None`` = 不改。改动此字段**单独提级**到系统管理员/租户管理员
    #: （AC-04）——部门管理员能改本子树内的 name/sort_order，但不能改这一项，否则
    #: 一级分行把自己翻成「部门」即可解除全局屏障，构成自下而上的提权（design 决策 11）。
    is_department: bool | None = None


class DepartmentNodeTypeBatchUpdate(BaseModel):
    """F062 (AC-08): set the 机构/部门 marker on several nodes at once.

    A batch, not N calls from the client, because the operator is asserting one
    intention over a selection: "these are all departments". N separate calls can
    stop halfway (one node rejected by the permission gate, one renamed by a
    concurrent edit) and leave a selection half-annotated, with whatever landed
    deciding who can see what. The endpoint is all-or-nothing instead.

    `dept_ids` are business keys, matching every other path in this module.
    """

    dept_ids: list[str] = Field(..., min_length=1, max_length=200)
    is_department: bool


class DepartmentMoveRequest(BaseModel):
    new_parent_id: int


class DepartmentAdminSet(BaseModel):
    """全量替换部门管理员；允许空列表表示清空。"""

    user_ids: list[int] = Field(default_factory=list)


class DepartmentMemberAdd(BaseModel):
    user_ids: list[int] = Field(..., min_length=1)
    is_primary: int = 0


class DepartmentLocalMemberCreate(BaseModel):
    """PRD §3.2.1：在部门内创建本地人员（主部门固定为当前部门）。"""

    user_name: str = Field(..., min_length=1, max_length=128)
    person_id: str = Field(..., min_length=1, max_length=128, description="人员ID（登录唯一凭证）")
    password: str = Field(..., description="RSA 加密后的密码（与 /user/create 一致）")
    role_ids: list[int] = Field(
        default_factory=list,
        description="可选；与部门「默认角色」合并后写入，均须落在当前部门可分配角色内",
    )


class DepartmentLocalMemberCreateWithDeptId(DepartmentLocalMemberCreate):
    """与 ``POST .../local-members`` 等价，但 ``dept_id`` 放在 body，避免路径中的 ``@`` 被网关误解析导致 404。"""

    dept_id: str = Field(..., min_length=1, description="部门外部 id，如 BS@hex")


class DepartmentTreeNode(BaseModel):
    id: int
    dept_id: str
    name: str
    parent_id: int | None = None
    path: str
    sort_order: int = 0
    source: str = "local"
    status: str = "active"
    # F011 mount-state surfaced to the frontend so DepartmentSettings can
    # toggle the "mark as Child Tenant" / "unmount" button, and DepartmentTree
    # can badge mounted nodes. mounted_tenant_id is None when is_tenant_root
    # is False; the backend keeps them in lockstep (see _set_mount_state in
    # DepartmentDao.aset_mount).
    is_tenant_root: bool = False
    mounted_tenant_id: int | None = None
    # F062: organization-tree node marker. `True` = 部门 (transparent to its
    # ancestors), `False` = 机构 (opaque barrier). Defaulted so every existing
    # consumer that constructs this model without the field keeps working — the
    # F038 lazy-tree path builds plain dicts (`_dept_node_dict`) and would raise
    # on a missing required field.
    is_department: bool = False
    # F038 lazy tree: has_children lets the frontend draw the expand affordance
    # without loading the child layer; matched flags search/locate hits in the
    # pruned tree. Both default False so existing (full-tree) consumers are unaffected.
    has_children: bool = False
    matched: bool = False
    children: list[DepartmentTreeNode] = []
