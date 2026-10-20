-- =============================================================================
-- 知识图谱（GraphRAG 简化版）增量 DDL
-- 目标数据库：MySQL 8.0+
-- 执行前请备份数据库。
-- 手动执行后如使用 Alembic，请确保迁移脚本带存在性守卫，或执行：
--   alembic stamp f091_knowledge_graph
-- =============================================================================

SET NAMES utf8mb4;

-- 1. knowledge 表新增图谱状态字段
ALTER TABLE knowledge
    ADD COLUMN graph_state    SMALLINT NOT NULL DEFAULT 0
        COMMENT '知识图谱状态 0未生成 1生成中 2已生成 3生成失败 4已过期',
    ADD COLUMN graph_built_at DATETIME NULL
        COMMENT '最近一次知识图谱成功生成时间',
    ADD COLUMN graph_job_id   BIGINT NULL
        COMMENT '最近一次知识图谱生成任务ID';

-- 2. 知识图谱生成任务表
CREATE TABLE IF NOT EXISTS knowledge_graph_job (
    id              BIGINT        NOT NULL AUTO_INCREMENT COMMENT '主键',
    tenant_id       INT           NOT NULL DEFAULT 1      COMMENT '租户ID',
    knowledge_id    INT           NOT NULL                COMMENT '知识库ID',
    status          SMALLINT      NOT NULL DEFAULT 0      COMMENT '0排队 1运行中 2成功 3部分失败 4失败 5取消中 6已取消',
    model_id        INT           NULL                    COMMENT '抽取使用的LLM模型ID',
    entity_types    JSON          NULL                    COMMENT '本次使用的实体类型列表',
    total_files     INT           NOT NULL DEFAULT 0      COMMENT '待处理文件数',
    done_files      INT           NOT NULL DEFAULT 0      COMMENT '已完成文件数',
    failed_files    INT           NOT NULL DEFAULT 0      COMMENT '失败文件数',
    total_chunks    INT           NOT NULL DEFAULT 0      COMMENT '待处理分块数',
    done_chunks     INT           NOT NULL DEFAULT 0      COMMENT '已处理分块数',
    token_in        BIGINT        NOT NULL DEFAULT 0      COMMENT '输入token数',
    token_out       BIGINT        NOT NULL DEFAULT 0      COMMENT '输出token数',
    error_message   VARCHAR(1000) NULL                    COMMENT '失败原因',
    create_user_id  INT           NULL                    COMMENT '发起人ID',
    create_time     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    start_time      DATETIME      NULL                    COMMENT '开始时间',
    end_time        DATETIME      NULL                    COMMENT '结束时间',
    update_time     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    KEY ix_kg_job_tenant (tenant_id),
    KEY ix_kg_job_kb_status (knowledge_id, status),
    KEY ix_kg_job_kb_id (knowledge_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识图谱生成任务';

-- 3. 知识图谱实体表
CREATE TABLE IF NOT EXISTS knowledge_graph_entity (
    id               BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    tenant_id        INT          NOT NULL DEFAULT 1      COMMENT '租户ID',
    knowledge_id     INT          NOT NULL                COMMENT '知识库ID',
    name             VARCHAR(255) NOT NULL                COMMENT '实体名称（原文）',
    normalized_name  VARCHAR(255) NOT NULL                COMMENT '归一化名称，用于实体合并',
    entity_type      VARCHAR(64)  NOT NULL                COMMENT '实体类型',
    description      TEXT         NULL                    COMMENT '实体描述',
    attributes       JSON         NULL                    COMMENT '属性键值对',
    aliases          JSON         NULL                    COMMENT '别名列表',
    mention_count    INT          NOT NULL DEFAULT 1      COMMENT '提及次数，用于Top-N排序',
    create_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_kg_entity (knowledge_id, normalized_name, entity_type),
    KEY ix_kg_entity_type (knowledge_id, entity_type),
    KEY ix_kg_entity_mention (knowledge_id, mention_count),
    KEY ix_kg_entity_name (knowledge_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识图谱实体';

-- 4. 知识图谱关系表
CREATE TABLE IF NOT EXISTS knowledge_graph_relation (
    id               BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    tenant_id        INT          NOT NULL DEFAULT 1      COMMENT '租户ID',
    knowledge_id     INT          NOT NULL                COMMENT '知识库ID',
    source_entity_id BIGINT       NOT NULL                COMMENT '源实体ID',
    target_entity_id BIGINT       NOT NULL                COMMENT '目标实体ID',
    rel_type         VARCHAR(64)  NOT NULL                COMMENT '关系类型',
    description      TEXT         NULL                    COMMENT '关系描述',
    weight           FLOAT        NOT NULL DEFAULT 1.0    COMMENT '关系置信度 0~1',
    attributes       JSON         NULL                    COMMENT '关系属性',
    mention_count    INT          NOT NULL DEFAULT 1      COMMENT '提及次数',
    create_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_kg_relation (knowledge_id, source_entity_id, target_entity_id, rel_type),
    KEY ix_kg_rel_source (knowledge_id, source_entity_id),
    KEY ix_kg_rel_target (knowledge_id, target_entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识图谱关系';

-- 5. 知识图谱证据表
CREATE TABLE IF NOT EXISTS knowledge_graph_evidence (
    id           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
    tenant_id    INT          NOT NULL DEFAULT 1      COMMENT '租户ID',
    knowledge_id INT          NOT NULL                COMMENT '知识库ID',
    entity_id    BIGINT       NULL                    COMMENT '实体ID；与relation_id二选一',
    relation_id  BIGINT       NULL                    COMMENT '关系ID；与entity_id二选一',
    document_id  BIGINT       NOT NULL                COMMENT '知识库文件ID（knowledgefile.id）',
    chunk_index  INT          NOT NULL                COMMENT '分块序号',
    chunk_pk     VARCHAR(128) NULL                    COMMENT 'ES文档_id，便于精确回查',
    confidence   FLOAT        NULL                    COMMENT '抽取置信度',
    create_time  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY ix_kg_ev_doc (document_id),
    KEY ix_kg_ev_entity (entity_id),
    KEY ix_kg_ev_relation (relation_id),
    KEY ix_kg_ev_kb (knowledge_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识图谱证据';

-- =============================================================================
-- 回滚（仅在需要撤销时执行，会删除已生成的图谱数据）
-- =============================================================================
-- DROP TABLE IF EXISTS knowledge_graph_evidence;
-- DROP TABLE IF EXISTS knowledge_graph_relation;
-- DROP TABLE IF EXISTS knowledge_graph_entity;
-- DROP TABLE IF EXISTS knowledge_graph_job;
-- ALTER TABLE knowledge
--     DROP COLUMN graph_job_id,
--     DROP COLUMN graph_built_at,
--     DROP COLUMN graph_state;
