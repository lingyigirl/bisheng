// Evaluation judge prompt templates (few-shot). The zh-Hans version explains
// the scoring method in Chinese; other locales keep the legacy English
// template unchanged.
//
// Two hard constraints from the backend:
// 1. The three placeholders {question}/{answer}/{ground_truth} are REQUIRED by
//    the create API and are replaced per test-case row at scoring time.
// 2. The prompt is formatted with a LangChain f-string template
//    (HumanMessagePromptTemplate.from_template), so literal JSON braces must
//    stay doubled ({{ }}) — single braces are treated as placeholders.

const EN_TEMPLATE = `Extract following from given question and ground truth

Question:What powers the sun and what is its primary function?
Answer: The sun is powered by nuclear fission, similar to nuclear reactors on Earth, and its primary function is to provide light to the solar system.
Ground truth: The sun is actually powered by nuclear fusion, not fission. In its core, hydrogen atoms fuse to form helium, releasing a tremendous amount of energy. This energy is what lights up the sun and provides heat and light, essential for life on Earth. The sun's light also plays a critical role in Earth's climate system and helps to drive the weather and ocean currents.
Extracted statements:
[
{{
  "statements that are present in both the answer and the ground truth": ["The sun's primary function is to provide light"],
  "statements present in the answer but not found in the ground truth": ["The sun is powered by nuclear fission", "similar to nuclear reactors on Earth"],
  "relevant statements found in the ground truth but omitted in the answer": ["The sun is powered by nuclear fusion, not fission", "In its core, hydrogen atoms fuse to form helium, releasing a tremendous amount of energy", "This energy provides heat and light, essential for life on Earth", "The sun's light plays a critical role in Earth's climate system", "The sun helps to drive the weather and ocean currents"]
}}
]

Question: What is the boiling point of water?
Answer: The boiling point of water is 100 degrees Celsius at sea level.
Ground truth: The boiling point of water is 100 degrees Celsius (212 degrees Fahrenheit) at sea level, but it can change with altitude.
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": ["The boiling point of water is 100 degrees Celsius at sea level"],
    "statements present in the answer but not found in the ground truth": [],
    "relevant statements found in the ground truth but omitted in the answer": ["The boiling point can change with altitude", "The boiling point of water is 212 degrees Fahrenheit at sea level"]
  }}
]

Question: What was the company's R&D expense as a percentage of revenue in 2021?
Answer: According to the information provided, the company's R&D expense accounted for 15.86% of revenue in 2021.
Ground truth: According to the company's prospectus data, the company's R&D expense accounted for 15.86% of revenue in 2021.
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": ["The company's R&D expense accounted for 15.86% of revenue in 2021"],
    "statements present in the answer but not found in the ground truth": [],
    "relevant statements found in the ground truth but omitted in the answer": []
  }}
]

Question: What was the EBITDA of Dameng in 2021?
Answer: Dameng's EBITDA in 2021 was 491,898,700 yuan.
Ground truth: According to Dameng Database's prospectus data, Dameng's EBITDA in 2021 was 491,898,500 yuan.
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": [],
    "statements present in the answer but not found in the ground truth": ["Dameng's EBITDA in 2021 was 491,898,700 yuan"],
    "relevant statements found in the ground truth but omitted in the answer": ["According to Dameng Database's prospectus data, Dameng's EBITDA in 2021 was 491,898,500 yuan"]
  }}
]

Question: What was Dameng's accounts receivable turnover ratio in 2022?
Answer: Based on the information provided, Dameng's accounts receivable turnover ratio in 2022 is unknown.
Ground truth: Sorry, Dameng has not yet disclosed its 2022 annual report data.
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": ["Dameng's 2022 accounts receivable turnover ratio is unknown"],
    "statements present in the answer but not found in the ground truth": [],
    "relevant statements found in the ground truth but omitted in the answer": [],
  }}
]


Question:{question}
Answer: {answer}
Ground truth: {ground_truth}
Extracted statements:"""`;

export const DEFAULT_EVALUATION_PROMPTS: Record<string, string> = {
  // Prompt content sent to the judge LLM is data, not UI copy — the zh-Hans
  // variant is Chinese by definition.
  // eslint-disable-next-line no-restricted-syntax
  "zh-Hans": `你的任务是评估一个答案相对标准答案的正确性。请把「答案」与「标准答案」分别拆解为若干条最小陈述（statement，一条陈述只表达一个事实点），按以下三类归纳，并只输出一个 JSON 数组：

- "statements that are present in both the answer and the ground truth"：答案与标准答案一致的陈述（命中）
- "statements present in the answer but not found in the ground truth"：答案中有、标准答案中没有的陈述（多答或编造）
- "relevant statements found in the ground truth but omitted in the answer"：标准答案中有、答案遗漏的陈述（漏答）

拆解规则：
1. 忠实原文：逐条拆出事实性陈述，不合并、不概括、不补充原文没有的内容。
2. 语义一致即命中：措辞、语序不同但含义相同，算同一条陈述（如「研发费用占营业收入的比例为15.86%」与「研发费用占比15.86%」）。
3. 数值以数值为准：数值一致即命中，单位差异（万元/元）不影响。
4. 纯客套与免责声明（如「仅供参考」「不构成授信决策依据」）不拆入任何一类。
5. 无陈述可归入某类时，该类别填空数组 []。

示例一（完全命中）：
Question: 公司2021年的研发费用占营业收入的比例是多少？
Answer: 根据提供的信息，公司2021年的研发费用占营业收入的比例为15.86%。
Ground truth: 根据公司招股书披露数据，公司2021年的研发费用占营业收入的比例为15.86%。
Extracted statements:
[
{{
  "statements that are present in both the answer and the ground truth": ["公司2021年的研发费用占营业收入的比例为15.86%"],
  "statements present in the answer but not found in the ground truth": [],
  "relevant statements found in the ground truth but omitted in the answer": []
}}
]

示例二（漏答）：
Question: 水的沸点是多少？
Answer: 水的沸点是100摄氏度。
Ground truth: 水的沸点是100摄氏度（海平面），并且会随海拔升高而降低。
Extracted statements:
[
{{
  "statements that are present in both the answer and the ground truth": ["水的沸点是100摄氏度"],
  "statements present in the answer but not found in the ground truth": [],
  "relevant statements found in the ground truth but omitted in the answer": ["水的沸点会随海拔升高而降低"]
}}
]

示例三（数值不符：答案数值与标准答案不一致）：
Question: 达梦2021年的息税折旧摊销前利润是多少？
Answer: 达梦2021年的息税折旧摊销前利润为49,189.87万元。
Ground truth: 根据达梦数据库招股书披露数据，达梦2021年的息税折旧摊销前利润为49,189.85万元。
Extracted statements:
[
{{
  "statements that are present in both the answer and the ground truth": [],
  "statements present in the answer but not found in the ground truth": ["达梦2021年的息税折旧摊销前利润为49,189.87万元"],
  "relevant statements found in the ground truth but omitted in the answer": ["达梦2021年的息税折旧摊销前利润为49,189.85万元"]
}}
]

示例四（要点型标准答案：把标准答案的要点拆成陈述后与答案匹配）：
Question:
Answer: 银行流水分析报告：账户流水共27笔，经营收入约308万元；收支结构以货款收入与采购支出为主；发现快进快出异常交易2对；偿债能力测算月均可支配现金流70,800元。
Ground truth: 应包含流水概览与收支结构的分析结论。
Extracted statements:
[
{{
  "statements that are present in both the answer and the ground truth": ["流水概览的分析结论", "收支结构的分析结论"],
  "statements present in the answer but not found in the ground truth": ["异常交易筛查的结论", "偿债能力测算的结论"],
  "relevant statements found in the ground truth but omitted in the answer": []
}}
]

Question:{question}
Answer: {answer}
Ground truth: {ground_truth}
Extracted statements:`,
  "en-US": EN_TEMPLATE,
  ja: EN_TEMPLATE,
};
