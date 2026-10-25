# EviWeave

**让数据分析代理从本地资料中寻找证据、完成分析，并交付可核验的结果。**

[English](README.md) · [实验说明](EXPERIMENTS.zh-CN.md) · [发布下载](https://github.com/yjdy/eviweave/releases/tag/v0.1.0)

## 1. 项目介绍

实际的数据分析经常需要同时处理表格、数据库、长文档和多媒体资料。EviWeave 将这些资料组织为独立任务，让代理检查证据、在隔离工作台中执行分析，最终输出完整的 CSV 结果。

项目提供：

- **证据驱动的分析流程**：读取任务内资料，完成检索、提取、关联和计算。
- **隔离执行环境**：使用固定工具工作台和沙箱，限制任务能够访问的文件与运行环境。
- **实验管理**：支持单题、批次运行、中断恢复和结果归集。
- **离线核验**：检查预测完整性，并使用冻结的评分材料评估已有答案。
- **离线费用分析**：导入用量或账单证据，按审阅后的定价计算和核对费用。

### DataSpace 成绩

EviWeave 首版公开了历史 V6 方法及其复现材料。该方法在 DataSpace 官方榜单中登记为 **codex-controlled + Qwen3.8-Flash**。

| 结果 | 分数 | 说明 |
|---|---:|---|
| 历史官方全量评测 | **333/410，81.22%** | 评测日期为 2026-10-21；2026-10-24 核验时并列第一 |
| 历史公开集首轮 | **44/60** | 保留的首次运行记录 |
| 历史公开集固定恢复后 | **45/60** | 对固定选定的历史预测进行离线评分 |

官方成绩与排名可查看 [DataSpace 榜单](https://dataspace-bench.github.io/#leaderboard)。排名会变化，上表记录的是带日期的历史结果。

**v0.1.0 已于 2026-10-24 发布。** 本版冻结历史 `relation-v6 + simple-fixes-v1`，使用 Codex **0.145.0**、**Qwen3.8-Flash** 和 Responses 协议。后续 EviWeave Controlled 将独立开发，不包含在本版中。榜单中的历史登记名与后续 Controlled 实现分别记录。

公开材料能够核验历史预测和公开集成绩。重新调用模型会形成新的实验；由于历史模型没有保存不可变服务快照，新运行不保证产生相同答案或再次取得 81.22%。

### 当前说明与发布版本

本页按已完成的发布与云端验收更新。v0.1.0 标签、安装包和历史来源材料保留原字节，其中部分文件仍使用发布准备时的表述。实际发布状态、云端结果与提交绑定见 [Release 正文及附件](https://github.com/yjdy/eviweave/releases/tag/v0.1.0)。下文三个准备入口已加入当前开发分支，云端验收尚待执行；它们未包含在已发布的 v0.1.0 标签或安装包中。

## 2. 安装与准备

使用下述新脚本需要包含三个入口的开发分支源码，当前尚无包含它们的新发行包。复验原始发布版本可从 **v0.1.0 源码或源码包**开始，并使用[原有准备工具](provenance/public-preparation.md)。单独安装 wheel 可以取得业务包和 `eviweave` / `dataspace-baselines` 两个命令入口；完整复现还需要源码中的脚本、资源锁和验证工具。下载入口为 [GitHub Release](https://github.com/yjdy/eviweave/releases/tag/v0.1.0)，本项目未发布到 PyPI。

### 2.1 环境准备

完整运行流程需要以下条件：

| 依赖 | 要求 |
|---|---|
| 操作系统 | Linux x86_64 |
| Python | 完整环境使用 CPython 3.12 或 3.13 |
| 宿主工具 | Docker/dockerd、containerd、Bubblewrap |
| 权限 | 能创建必要的 namespace 和挂载，并为项目内 daemon 启动器提供所需免密权限 |
| 模型服务 | 运行真实实验时，提供兼容的 HTTPS Responses 端点及专用 API key |

云端已验证 CPython **3.12.3、3.13.16** 的完整适用检查，以及 **3.10.22** 的包级适用检查。Python 3.10 的结果不代表完整工作台流程受到支持。macOS、Windows 和 ARM 尚未验收。

环境安装分为四步：

1. 获取包含新脚本的源码；将目标 CPython 3.12 或 3.13 作为下文的 `python3`。
2. 由开发者或系统管理员安装上述宿主依赖。
3. 先检查静态前置条件，再准备工作台、登记档案并安装：

```sh
./scripts/prepare_environment.sh --python python3 --check-only
```

```sh
./scripts/prepare_environment.sh --python python3
```

4. 成功后，查看终端给出的 `.audit/preparation/.../preparation-report.json`，再按第 2.3 节显式选择档案和模型配置。

**当前验收状态：代码已加入开发分支，云端使用与安装验收尚未执行。** `--check-only` 不联网、不写文件，也不验证免密权限或沙箱能力；实际准备时才执行这些检查。

默认创建 `.venv-experiment`、`.local/profiles/public-host.json`、`.local/config-v6/offline-public-host.yaml`，并将 Codex、工作台和独立 daemon 存储放在 `.runtimes/prepared/`。准备只执行离线 doctor，不下载评分材料或完整题目数据。

已有目标需显式添加 `--reuse`；冲突或损坏直接拒绝，不覆盖已有环境。可用 `--venv`、`--profile-name`、`--runtime-root` 和 `--cache` 选择独立目录，详情见 `--help`。已有 `.local/config-v6/codex.yaml` 保留；离线配置只复用本脚本登记摘要后未变化的文件。

脚本不会自动安装系统工具、激活档案、改写 `.local/env.sh` 或调用模型。档案记录的是本次重建环境，不能认作历史实验原始主机。所有下载、HOME、缓存、临时文件和 Docker/containerd 存储留在项目内，不接系统 Docker socket。

脚本要求至少 8GiB 可用空间作为初始检查，实际峰值可能更高。它会停止本次启动的 daemon，保留原先已运行的实例；后续镜像检查按[脚本使用说明](provenance/preparation-scripts.md)使用同一存储重新启动。环境与评分材料准备完成后，可按该说明执行无模型合成测试。

### 2.2 数据准备

DataSpace 的题目数据和评分材料分开准备：

| 材料 | 用途 | 何时需要 |
|---|---|---|
| 121 项冻结评分文件 | 评估已有预测 | 离线评分 |
| 完整题目数据 | 向代理提供问题和资料 | 新的模型运行 |
| 历史证据附件 | 核验已发布的预测和历史结果 | 历史结果复验 |

完整题目 ZIP 约 **2.88GB**，解压内容约 **15.01GB**，另需工作台和输出空间。只复验历史成绩，无需下载完整题目数据，也无需构建模型运行工作台。

默认仅准备评分材料，可先检查静态前置条件：

```sh
./scripts/prepare_dataspace.sh --python python3 --check-only
```

```sh
./scripts/prepare_dataspace.sh --python python3
```

只有需要新模型实验时才明确选择完整数据：

```sh
./scripts/prepare_dataspace.sh --python python3 --mode full
```

**完整数据模式的新增包装层将使用人工小型归档验证，本轮不重新下载约 15GB 完整数据；云端验收尚待执行。** `--reuse` 显式核验已有目标，评分目录、数据目录和缓存分别通过 `--evaluation-root`、`--dataset`、`--cache` 指定。复用完整数据需要缓存中保留摘要匹配的原 ZIP，以逐文件比对；不会因 ZIP 缺失自动补下载。

建议保持以下目录约定：

| 目录 | 内容 |
|---|---|
| `data/DataSpace-Benchmark/` | 完整题目数据，目录下含 `input/` |
| `.local/evaluation-v6-public60/` | 冻结评分材料 |
| `.local/evidence-v6/` | 下载的历史证据附件 |
| `.local/profiles/` | 本机环境档案 |
| `.local/config-v6/` | 私有运行配置 |
| `.audit/` | 检查记录、实验输出和报告 |

已经准备好的数据可以在核验后复用。发现文件摘要不符或目标目录冲突时应停止检查，保留原材料。评分材料的评估器和数据 revision 固定在[公共资源锁](provenance/public-resources-lock.json)，121 项内容另按[评分锁](provenance/evaluation-lock.json)核对。参考答案不放入求解工作区。

### 2.3 配置模型访问

只做离线验证和合成测试时，无需 API key。

运行真实实验前：

1. 在 `.local/config-v6/codex.yaml` 的 `experiment.model.openai_base_url` 中配置兼容的 HTTPS Responses 基础地址。
2. 在终端设置专用 `EVIWEAVE_API_KEY`。
3. 检查 `.local/env.sh`，选用准备好的环境档案及数据路径。已有私有配置按设计保留；运行时路径须与所选档案一致。

统一入口使用以下设置：

| 设置 | 用途 / 本文约定 |
|---|---|
| `EVIWEAVE_PYTHON` | 项目内 `.venv-experiment/bin/python` |
| `EVIWEAVE_ENVIRONMENT_PROFILE` | `.local/profiles/` 下采集并检查通过的档案 |
| `EVIWEAVE_CONFIG` | `.local/config-v6/codex.yaml` |
| `EVIWEAVE_DATASET` | `data/DataSpace-Benchmark/`，即 `input/` 的父目录 |
| `EVIWEAVE_EVALUATION_ROOT` | `.local/evaluation-v6-public60/` |
| `EVIWEAVE_API_KEY` | 专用 key；私有模板保持空白可继承终端值 |

路径设置建议使用项目内绝对路径。非空 `.local/env.sh` 设置覆盖继承的环境变量；显式 CLI 参数优先。安装引导解释器另由 `EVIWEAVE_BOOTSTRAP_PYTHON` 选择。

API key 不写入模型 YAML、题目资料或公开日志。项目只使用专用 `EVIWEAVE_API_KEY`，不搜索其他项目的凭据。服务地址不含内嵌凭据、query 或 fragment。

复现 V6 时，应保留冻结的模型名称、提示词、32,768 输出 token、983,616 上下文 token、单题 1,800 秒和并发 1。详细环境与资源身份说明见[资源准备文档](provenance/public-preparation.md)。

<a id="统一实验流程"></a>

## 3. 以 DataSpace 为例复现实验

建议先验证历史材料，再决定是否投入费用运行新的实验。本节从源码根目录执行；真实实验命令假定已按上一节设置 `.local/env.sh`。

### 3.1 离线复验历史结果

从 [v0.1.0 Release](https://github.com/yjdy/eviweave/releases/tag/v0.1.0) 获取：

- `eviweave-v0.1.0-v6-evidence.zip`
- `eviweave-v0.1.0-v6-evidence.zip.sha256`

将两者放入 `.local/evidence-v6/`。附件包含 **410 份预测、414 条尝试摘要、固定选择记录，以及 5 题、6 次尝试的精选脱敏轨迹**。

历史评分要求 CPython 3.12.3。若 `python3` 是其他版本，先将 Shell 变量 `HISTORY_PYTHON` 设置为已准备的 3.12.3 解释器绝对路径；脚本不会安装该解释器。

显式允许从固定公开 Release 获取附件，并执行校验和评分：

```sh
./scripts/verify_history.sh --python "${HISTORY_PYTHON:-python3}" --download
```

已有附件时省略 `--download`，默认完全离线。可用 `--archive`、`--evaluation-root` 和 `--output` 显式选择输入与全新输出，或先用 `--check-only` 做不写入的静态检查。缺 ZIP、缺外部摘要、摘要不符或评分逐题不一致均失败。

脚本无需安装业务包、完整工作台或题目数据，只依赖源码工具、标准库和准备好的 121 项评分材料。报告登记原附件 SHA-256 和逐题匹配结果；新入口的云端复验尚待执行。

完成后应得到：

- 公开集 **45/60**。
- 15 题未通过。
- 其余 350 题没有公开参考答案，本地不评分。

这一步验证历史公开集结果。完整 **333/410** 来自官方评测，需要官方持有的非公开评分材料，无法仅靠公开数据在本地重新计算。

历史四题 **22、23、207、273** 因缺少输出各恢复一次，固定采用第二次尝试；其他题目采用第一次尝试。历史首轮 **44/60** 单独保留，不按评分结果重新挑选预测。

### 3.2 检查环境，再运行新实验

环境准备后的合成测试使用本地执行器，不调用模型，用于检查沙箱、CSV 输出、结果归集和恢复流程。合成测试的 2/2 属于人工样例结果。

开始真实运行前，先执行单题前置检查：

```sh
./scripts/experiment.sh check --tasks 33
```

检查通过只表示本地前置条件满足，**不会认证模型服务，模型调用数仍为零**。生产检查需要 key 和有效端点；缺 key 时返回 `MISSING_KEY`，无 key 检查应使用离线 doctor。

确认配置和费用预算后，运行单题：

```sh
./scripts/experiment.sh run --tasks 33 --output .audit/task33-v6
```

运行公开 60 题：

```sh
./scripts/experiment.sh run --scope public60 --output .audit/public60-v6
```

如需准备全量官方评测结果，明确选择全部 410 题，并使用独立输出：

```sh
./scripts/experiment.sh run --scope all --output .audit/all410-v6
```

**以上 `run` 命令会调用真实模型并产生费用。** 不指定题目或范围时默认运行 public60，`--repeat 1`。建议始终显式选择范围，先完成单题检查；不要用未限定范围的运行测试安装。

新运行应独立报告模型、环境、日期、题目范围和得分，不与历史 333/410 或 45/60 合并。

### 3.3 中断与恢复

实验中断后，可以对原目录续跑：

```sh
./scripts/experiment.sh run --output .audit/public60-v6 --resume --yes
```

续跑会恢复原实验保存的题目、配置和环境身份，补充未完成或执行失败的任务。正常提交但答案错误的题目会跳过；全部完成后再次续跑应零新增尝试。续跑拒绝显式替换环境档案等冻结设置。每次新分配尝试都会成为本题当前选择，失败时也一样。

不要通过修改旧实验配置、替换预测或挑选最好一次来提高报告分数。Ctrl+C 中断返回 130。最终 401/402/403 会在当前任务确认失败后停止批次并取消在途任务；修复访问问题后再有意续跑。429/500 按普通执行失败处理。

### 3.4 验证与整理成果

对新实验执行离线验证：

```sh
./scripts/experiment.sh verify --experiment .audit/public60-v6 \
  --output .audit/public60-v6-verification
```

验证会检查实验身份、预测完整性和公开题目得分，生成 `summary.json`、`score.json`、`summary.csv`。已有统一实验的验证无需 key、原数据、原配置或模型运行时，也不会重新调用模型。

需要区分：

- `submitted`：任务执行及 CSV 格式校验通过。
- 答案正确：预测通过冻结评分规则。

运行全部 410 题时，本地仍只能对其中公开的 60 题评分。缺失预测应保留为失败，不能从统计分母中删除。

### 3.5 计算与核对费用

当前版本支持**离线费用分析**。开发者显式提供脱敏的请求用量证据和已审阅定价后，可以导入分析：

```sh
./scripts/experiment.sh cost import \
  --source .local/import/calls.json --source-type calls \
  --pricing .local/pricing/reviewed.json --output .audit/call-analysis
```

检查证据是否足以支持提交费用声明：

```sh
./scripts/experiment.sh cost submission-check \
  --experiment .audit/call-analysis
```

更换定价后，重估到新目录：

```sh
./scripts/experiment.sh cost revalue \
  --experiment .audit/call-analysis \
  --pricing .local/pricing/reviewed.json --output .audit/call-revaluation
```

[定价模板](configs/pricing.example.json)中的空值需要按服务商公开价格填写，并记录来源和适用日期。分析应区分按公开价格计算的费用、服务商返回金额与账户实际支付金额。

请求记录不完整时，工具应报告费用不完整；未知用量不补零。历史材料有三次用量缺失，不能据此还原完整费用。当前完整在线采集仍为 `UNSUPPORTED_G1`，运行结束不会自动得到经过认证的全量请求费用。

新分析使用 v2，旧 v1 分析保持只读，验证和重估写入全新独立输出。每个物理请求先合计费用组件，再按精确步长（含 0.05）舍入一次，最后跨请求汇总。详细计算规则见[费用分析说明](provenance/observability.zh-CN.md)和[舍入政策](provenance/config-rounding.zh-CN.md)。

### 3.6 向 DataSpace 提交成果

新的官方榜单申请应按照 [DataSpace 官方提交指南](https://dataspace-bench.github.io/submission-guidelines.html)准备，主要包括：

1. 全部 410 题各一份最终预测及对应完整执行轨迹。
2. 方法、模型、运行日期和预先确定的结果选择政策等元数据。
3. 包含失败调用和重试的用量、费用记录，以及官方定价来源。
4. 公开 60 题的本地验证结果。

预测按 `predictions/task_N/prediction.csv` 组织，官方包还要求 `metadata.json` 和 `traces/`。统一实验会归集预测，但当前没有自动生成并发送完整官方投稿包的功能，仍需核对轨迹、费用和元数据后，按官方渠道提交。

公开历史证据 ZIP 只有精选轨迹，不能直接作为新的完整投稿包。费用完整性检查也不代表官方投稿已经通过审核。历史用量和模型快照的缺口应如实披露，不能补造。

### 3.7 复现范围与常见问题

| 情况 | 建议处理 |
|---|---|
| 仅想确认历史结果 | 下载证据附件和评分材料，执行离线复验 |
| 磁盘有限 | 不重复下载完整题目数据，先选择离线复验路径 |
| 缺工具或沙箱权限 | 补齐宿主条件，不回退到宿主直接执行 |
| 环境档案不匹配 | 检查实际解释器和运行时，登记独立环境 |
| 运行时路径仍旧 | 已有 YAML 按设计保留；生成全新离线配置并显式选择 |
| 缺少预测 | 保留失败记录，确认原因后有意续跑 |
| 文件摘要不符或缓存冲突 | 停止使用该材料，核查来源，不覆盖历史资源 |
| 完整性冲突 | 验证失败并保留证据，不修改原记录制造通过 |
| 锁占用 | 等待写入者结束，不对同目录并发 run、verify 或 revalue |
| 费用证据不完整 | 明确报告未知范围，不声称全量费用已认证 |
| 旧实验源码身份不匹配 | 使用原冻结工具版本或新建实验，不绕过检查 |

历史模型别名没有对应的不可变服务快照；新宿主环境也拥有独立身份。因此，代码、环境检查、历史预测核验和新实验得分应分别记录。更多历史恢复、后续独立运行及证据限制见[实验说明](EXPERIMENTS.zh-CN.md)。

上述实验命令退出 0 表示操作完成，不表示答案全部正确；1 表示执行、证据、评分或报告失败，2 表示参数或前置条件失败，130 表示中断。

## 4. 后续计划

后续工作围绕以下方向推进：

- **完善使用入口**：完成上述三个新脚本的云端验收，依据实际反馈改善诊断和准备流程。
- **持续验证兼容性**：在已验版本基础上，逐步验证更多 Python 补丁版本和宿主环境，整理面向后续 main 变更的常规 CI。
- **完善费用观测**：补齐逐次请求的持久化、用量和金额证据，通过验收后再启用完整在线采集。
- **开发 EviWeave Controlled**：与冻结 V6 分开演进，保持清楚的版本及实验边界。
- **补充研究材料**：整理正式技术报告和更完整的方法分析。

这些项目属于后续工作，不代表 v0.1.0 已提供相应能力。历史云端工作流仍验证固定 v0.1.0；新增准备脚本工作流将检验其实际候选提交，目前尚未运行。

---

EviWeave 作者：**袁珺 / Jun Yuan**。代码与文档采用 [MIT](LICENSE)，保留 DataSpace Authors 的声明与来源归属。数据、预测和第三方运行时组件遵循各自条款；本项目 MIT 不为其重新授权。数据来自[官方固定版本](https://huggingface.co/datasets/HKUSTDial/DataSpace/tree/96be19257d7aa251dbce4771d46a26fc36f6dc52/release)，业务源码来源为历史冻结 V6。

私有 `docs/`、审计、数据、配置和运行时目录不进入 Git 或发行包。

[引用本项目](CITATION.cff) · [源码来源](provenance/README.zh-CN.md) · [自定义数据格式](DATA_FORMAT.md) · [发布说明](RELEASE_NOTES.md)
