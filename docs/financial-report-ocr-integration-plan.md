# 外部财务报表 OCR 接入方案（第一阶段）

> 状态：第一阶段已实施
> 范围：仅解决外部 OCR 接入、JSON 转 Markdown、路由和入库链路贯通
> 原则：最小化改动，不影响现有 MinerU / PaddleOCR / ETL4LM / 本地解析链路

## 1. 背景

当前知识库解析链路为：

    上传原文件
      -> Loader 解析
      -> Document(page_content, metadata)
      -> 切分
      -> 向量化
      -> Milvus / Elasticsearch 入库
      -> 检索

当前部署实际配置为：

    loader_provider = mineru
    image_parser_enabled = True
    mineru.url = http://172.19.0.3:8010/file_parse

新增的外部财务报表 OCR 接口返回 JSON，不直接返回 Markdown。经确认，该 JSON 包含以下典型结构：

    data.results[].data
    data.results[].column_info
    data.results[].page_no
    data.results[].description
    data.results[].type

现有 MinerU / PaddleOCR / ETL4LM Loader 不能直接识别该响应结构，因此需要新增专用 Loader 和 JSON 转 Markdown Adapter。

## 2. 第一阶段目标

只完成以下闭环：

    新增 provider 路由
    + 新增 FinancialReportOcrLoader
    + 新增 FinancialReportJsonAdapter
    + JSON 转 Markdown
    + 返回现有 Document
    + 复用现有切分、向量化、入库、检索

本阶段暂不处理：

- 财务长表格专项切分
- 每个 chunk 重复表名和列头
- 多级表头、合并单元格优化
- 数值检索优化
- OCR bbox 高亮适配
- 自动识别 JSON 类型
- 文件级或知识库级 provider 选择界面

## 3. 路由开关

复用现有配置开关：

    knowledges:
      loader_provider: "mineru"

启用外部财务报表 OCR 时配置为：

    knowledges:
      loader_provider: "financial_report_ocr"

      financial_report_ocr:
        url: http://xxx/ocr/v1/financial_report
        timeout: 60
        headers: {}

路由判断：

    loader_provider == financial_report_ocr
    且
    financial_report_ocr.url 已配置
      -> 使用 FinancialReportOcrLoader

    loader_provider == mineru
      -> 使用现有 MineruLoader

    loader_provider == paddle_ocr
      -> 使用现有 PaddleOcrLoader

    loader_provider == etl4lm
      -> 使用现有 Etl4lmLoader

    其他情况
      -> 使用现有默认逻辑

如果选择 financial_report_ocr 但未配置对应 URL，建议明确报配置错误，不静默回退到本地解析器。

## 4. 配置读取位置

### 4.1 初始化配置模板

/home/host/jinbao/src/backend/bisheng/initdb_config.yaml

### 4.2 运行时实际配置

首次初始化时，initdb_config.yaml 会写入 MySQL：

    config 表
      key   = initdb_config
      value = YAML 配置内容

运行时通过 Redis 缓存读取：

    config:initdb_config

因此，对已经初始化过的环境，不能只修改 initdb_config.yaml，需要通过系统配置保存接口更新 MySQL 配置：

    POST /api/v1/config/save

保存后会删除 Redis 缓存，使新配置生效。

### 4.3 配置模型

/home/host/jinbao/src/backend/bisheng/core/config/settings.py

新增或扩展 KnowledgeConf：

    loader_provider: str = Field(default="etl4lm")
    financial_report_ocr: FinancialReportOcrConf

### 4.4 Loader 路由

/home/host/jinbao/src/backend/bisheng/knowledge/rag/base_file_pipeline.py

现有 _build_pdf_loader() 已按 loader_provider 动态选择解析器，新链路在该方法中增加分支即可。

## 5. 最小改动清单

### 5.1 新增 ParseType

/home/host/jinbao/src/backend/bisheng/knowledge/domain/models/knowledge_file.py

    FINANCIAL_REPORT_OCR = "financial_report_ocr"

### 5.2 新增配置模型

/home/host/jinbao/src/backend/bisheng/core/config/settings.py

新增：

    class FinancialReportOcrConf(BaseModel):
        url: str = ""
        timeout: int = 60
        headers: dict = {}
        request_kwargs: dict = {}

并在 KnowledgeConf 中增加：

    financial_report_ocr: FinancialReportOcrConf

### 5.3 新增 Loader

建议路径：

    /home/host/jinbao/src/backend/bisheng/knowledge/rag/pipeline/loader/financial_report_ocr.py

职责：

1. 读取上传后的本地原文件；
2. 调用 /ocr/v1/financial_report；
3. 校验响应：

    e.code == 200
    data.code == "00000"
    data.results 非空

4. 调用 FinancialReportJsonAdapter 转 Markdown；
5. 返回：

    [Document(page_content=markdown, metadata=self.file_metadata.copy())]

### 5.4 新增 JSON Adapter

职责：

1. 遍历 data.results；
2. 使用 column_info 作为表头；
3. 使用 data 中每行的 words 生成 Markdown 表格；
4. 每个 result 可以生成一个 Document，或合并为一个 Markdown；
5. 返回标准 Document，不在 Adapter 内做向量化或入库。

### 5.5 增加 Loader 路由

/home/host/jinbao/src/backend/bisheng/knowledge/rag/base_file_pipeline.py

增加：

    elif (
        knowledge_conf.loader_provider == ParseType.FINANCIAL_REPORT_OCR.value
        and knowledge_conf.financial_report_ocr.url
    ):
        return FinancialReportOcrLoader(...)

分支顺序不影响现有 provider，因为只有配置为 financial_report_ocr 时才会进入。

### 5.6 更新图片解析能力

/home/host/jinbao/src/backend/bisheng/core/config/settings.py

KnowledgeConf.image_parser_enabled 增加：

    provider == financial_report_ocr
    且 financial_report_ocr.url 已配置
      -> True

否则：

- 前端可能不会展示图片为可上传格式；
- Task Scheduler 可能不会将图片送入 OCR 队列。

## 6. 不影响其他链路的依据

新 Loader 必须遵守现有契约：

    def load(self) -> list[Document]:
        ...

后续链路只依赖：

    Document(
        page_content="文本或 Markdown",
        metadata={...},
    )

因此以下环节不需要感知外部 OCR：

- 上传接口
- MinIO 原文件保存
- MySQL 文件记录
- 文件状态流转
- 摘要、内容安全、SimHash
- 通用切分
- 向量化
- Milvus 入库
- Elasticsearch 入库
- RRF、rerank、检索

## 7. 前端影响

第一阶段不需要改前端。

原因：

- 上传接口不传 loader_provider；
- provider 由后端配置决定；
- 当前前端已支持 PDF 和图片；
- 只要后端 image_parser_enabled 识别新 provider，前端沿用现有 enable_etl4lm 能力即可。

以下需求才需要前端改动：

- 用户手动选择 OCR provider；
- 按知识库配置 provider；
- 管理端新增 provider 配置界面；
- 新增 PDF / 图片之外的格式。

## 8. 当前已知风险

1. 外部 OCR 服务超时或错误时，需要映射到现有解析失败状态，避免文件一直停留在 processing。
2. 响应 JSON 结构变化时需要明确报错，不要静默生成空 Document。
3. 第一阶段不处理长表格切分，检索效果可能暂时不稳定。
4. 如果未来需要同时使用 MinerU 和财务报表 OCR，loader_provider 作为部署级全局开关不够，需要额外增加知识库级或上传级路由键。

## 9. 验收标准

1. loader_provider=mineru 时，原 MinerU 流程保持正常。
2. 切换 loader_provider=financial_report_ocr 并配置 URL。
3. 上传财务报表 PDF 或图片。
4. 外部 OCR 调用成功。
5. JSON 成功转换为 Markdown。
6. Loader 返回非空 Document。
7. MySQL 文件状态变为成功。
8. Milvus 和 Elasticsearch 中存在对应 chunk。
9. 检索接口可以返回命中的文档片段。

## 10. 后续阶段

基础接入验证通过后，再处理：

- 财务长表格按完整行切分；
- 每个 chunk 重复表名和列头；
- 多级表头拼接；
- 续页继承表头；
- 期间、单位、表类型写入 user_metadata；
- 数值检索优化；
- OCR bbox 对齐与高亮。

## 11. 实施记录（2026-09-23）

已完成第一阶段代码接入：

- 新增 ParseType.FINANCIAL_REPORT_OCR。
- 新增 FinancialReportOcrConf 配置模型。
- loader_provider=financial_report_ocr 时启用财务报表 OCR 路由。
- 新增 FinancialReportOcrLoader：
  - 支持 multipart 请求。
  - 支持 base64_json 请求。
  - 校验 e.code、data.code 和 data.results。
  - 输出标准 Document。
- 新增 FinancialReportJsonAdapter：
  - 将 data.results 转为 Markdown 表格。
  - 使用 column_info 作为表头。
  - 过滤全空行。
- 更新 image_parser_enabled，使新 provider 支持图片解析能力判断。
- 更新 initdb_config.yaml 示例配置。

验证结果：

- ruff format --check 通过。
- ruff check 通过。
- pytest test/knowledge/rag/test_financial_report_ocr.py：7 个用例通过。
- 使用用户提供的 12 个 result 财务报表 OCR 样例完成转换验证，成功生成 16343 字符 Markdown。

当前运行环境尚未切换 provider，实际生效配置仍为 mineru。启用新链路需要更新 MySQL 中的 initdb_config 配置，并将 knowledges.loader_provider 设置为 financial_report_ocr，同时配置 financial_report_ocr.url。
