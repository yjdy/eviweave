# 逐请求证据与离线费用工具

作者：袁珺 / Jun Yuan；代码与说明 MIT。来源覆盖层见
`observability-baseline.json`，不改写此前 P4/隔离/统一实验的来源记录。

本阶段交付离线导入、原始数字保留、显式字段映射、去重/冲突检测、
Decimal 计价、账单对账、分层报告及重估。固定 Codex 0.145.0 的隔离假服务诊断
支持 OTLP/HTTP JSON，但未保留完整 usage 扩展/返回金额，也未证明请求前持久化。
完整在线采集 G1 未通过；当前没有认证的在线 profile，开启时在模型启动前拒绝。
统一格式 v2 仅保留合同定义，没有可运行的 v2 实验；v1 运行和验证继续走原路径。
离线分析格式 `eviweave-call-analysis` 不可 run/resume，不声称曾在线采集完整。
新建分析容器为schema_version=2、import_identity_version=2；请求合同仍为v1。
导入身份由来源类型、脱敏字节SHA与物理请求ID集合共同确定；不同物理请求即使响应字节相同也分别保存和计数。

所有以下命令均为离线操作，`observe-check` 的固定客户端只连接隔离网络内的人工服务：

```sh
./scripts/experiment.sh observe-check --output .audit/observe-check-new

# 已有 v1 的任务级用量快照；独立输出，不更改原实验。
./scripts/experiment.sh cost import --experiment .audit/my-v1 \
  --source-type legacy --output .audit/my-legacy-analysis

# 用户显式提供的逐请求证据。默认映射为 Qwen Responses；价格可省略。
./scripts/experiment.sh cost import --source .local/import/calls.json \
  --source-type calls --usage-mapping .local/import/mapping.json \
  --pricing .local/pricing/reviewed.json --output .audit/my-call-analysis

# 原始单次 JSON/SSE 响应需另提供强关联元数据。
./scripts/experiment.sh cost import --source .local/import/response.sse \
  --source-type responses-sse --metadata .local/import/request.json \
  --output .audit/my-response-analysis

./scripts/experiment.sh cost import --experiment .audit/my-call-analysis \
  --source .local/import/billing.csv --source-type billing \
  --output .audit/bill-import-receipt
./scripts/experiment.sh verify --experiment .audit/my-call-analysis
./scripts/experiment.sh cost revalue --experiment .audit/my-call-analysis \
  --pricing .local/pricing/another.json --output .audit/my-revaluation
./scripts/experiment.sh cost submission-check --experiment .audit/my-call-analysis

# 旧schema_version=1分析目录只读，输出必须全新、独立；源目录须已有锁文件。
./scripts/experiment.sh verify --experiment .audit/old-v1-analysis --output .audit/old-v1-verification-new
./scripts/experiment.sh cost revalue --experiment .audit/old-v1-analysis \
  --pricing .local/pricing/another.json --output .audit/old-v1-revaluation-new
```

文件必须位于项目内，无符号链接，单文件上限 8 MiB。原件由用户自行保管；
工具保存脱敏后的原始字节及 SHA-256。JSON 数字词法、小数、嵌套数组和未知扩展保留；
重复键、非法数值及不支持的格式拒绝，不猜测字段含义。
没有 API key 或 DataSpace 也可验证已导入的分析目录。

`calls` 源文件合同为：

```json
{
  "kind": "eviweave-call-import",
  "schema_version": 1,
  "requests": [{
    "request_attempt_id": "local-physical-request-1",
    "experiment_id": "experiment-1",
    "plan_id": "plan-1",
    "launch_id": "launch-1",
    "task_id": "task_33",
    "task_attempt_id": "tasks/task_33/run_1",
    "interaction_id": "turn-1",
    "retry_index": 0,
    "retry_of": null,
    "started_at": "2026-10-23T00:00:00Z",
    "identity": {
      "provider": "aliyun", "model": "qwen3.8-flash", "region": null,
      "deployment": null, "protocol": "responses", "mode": "sync"
    },
    "events": [{
      "producer": "raw-response", "sequence": 1, "event_id": "event-1",
      "kind": "response.completed", "terminal": true,
      "payload": {"response": {"usage": null}}
    }]
  }]
}
```

JSON/SSE 的 metadata 使用上述请求字段，省略 events。缺失的关联、时间、
HTTP 状态和服务商身份保留 null，不按时间邻近分配。提供的本地 ID 必须代表
不同物理 HTTP 尝试；相同 response_id 不使真实重试被去重。多来源存在冲突时
不宣布完整费用；更正终止事件需要显式 supersedes 对应 event_id。
累计值只取唯一终止依据；只有映射明确声明 incremental 时才累加不重叠增量。

定价 JSON 参照 `configs/pricing.example.json`。模板费率为 null，不能直接计价。
必须审阅适用模型、地区、部署、模式、日期、来源 URL/时间/摘要、收费单位及重叠关系。
字段映射允许显式 not_applicable，不将未知缓存创建量默认为零。
金额与费率均为十进制字符串；结果精度上限与数量大小受合同限制。
公开价格只按唯一匹配规则计算；未知、冲突和重叠均阻止完整金额。

`rounding.quantum` 为正的有限十进制字符串步长，例如 `"0.05"`；null 表示不额外舍入。
每个物理请求先合计组件，再按步长舍入一次，最后跨请求汇总。half_even 在中点选择偶数倍，
half_up 在中点向较大倍数舍入，truncate 对非负金额向零舍入；结果必须为步长整数倍。
步长判定采用精确整数运算；若组件合计已超出精度而丢失有效位，则明确拒绝本次步长计价。
新费用结果与报告登记 `calculation_policy_version=decimal-quantum-v2`，逐请求保留
`unrounded_amount`。定价本身的 pricing_id/rule_version 和服务商/账单金额不变。
旧报告缺少该版本字段时不追认为已修正；保留旧件，使用相同审阅定价和全新 `--output`
执行 `cost revalue`。旧分析v1继续只读，原始证据与定价不改写。

账单 CSV 或 `{"rows":[...]}` JSON 每行需 bill_id、grain、amount_kind、amount、currency、
period_start、period_end。时间带时区；grain 为 request/minute/hour/day/month/account；
amount_kind 为 actual_paid/payable/list/credit/tax。只有 request 粒度、明确
request_attempt_id 和 association_evidence 且唯一的 actual_paid 行进入逐请求实际金额。
聚合行及非实际支付字段保留未分配，不均摊。更正以新 bill_id 和 supersedes 保留历史。

报告一次提交八个成员：summary.json、score.json、summary.csv、usage.json、cost.json、
calls.csv、cost-breakdown.csv、analysis.json；report-set.json 登记相同报告/证据版本及摘要。
原始导入、追加日志、检查点、派生 generation 和旧报告保留；重复导入/验证不重复计费。
尾部半行保持原样并报告缺口；原始证据损坏失败，不截断或修复成成功。
任务/尝试/计划/启动/实验分组仅针对已观测请求；legacy 汇总另列，不与请求量相加。

三条金额路径分别为 provider_reported、public_list_calculated、billing_actual；
币种分别汇总。全请求枚举未认证时总请求数为 null，观测数是下界。
已知子集可给出小计，但整体 cost_complete 永远保留 false。
候选提交检查返回非零及诊断，不输出可用于正式提交的 total_usd。
账单导入不访问控制台，真实鉴权、服务字段和账户扣款核对均为 NOT_TESTED。

退出码：0 为按声明等级完成（金额可能未知），2 为参数/前置条件失败，
1 为运行/证据/计算/报告失败或严格提交检查不合格，130 为用户中断。
普通 check 不启动诊断客户端；run 的观测参数因 G1 阻断，绝不自动降级。
新工具改变了外层源码身份；旧 v1 若要续跑，需使用其原冻结工具版本或新建目录。
这里的可续跑v1指统一实验格式；费用分析v1本身始终不可续跑。旧费用目录禁止追加导入和原地升级，
验证/重估使用只读共享锁，generation与报告全部写入新输出；缺锁或参数不合要求时拒绝且不写源目录。
普通JSON/SSE或账单原件可以显式导入新建的v2分析目录；不会自动搬迁旧证据或改变旧报告。
