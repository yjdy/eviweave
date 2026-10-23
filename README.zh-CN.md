# EviWeave — Evidence-Guided Data Agents

[English](README.md)

EviWeave 面向异构本地资料执行数据分析：检查证据、在沙箱内运行分析，并以无损 CSV 提交结果。首版保留历史 **relation-v6 + simple-fixes-v1** 基线，使用 **Codex 0.145.0**、**Qwen3.8-Flash** 与 **Responses** 协议，提供显式资源准备、环境档案、实验运行/续跑、离线验证和离线费用分析。

**v0.1.0 待发布。** 文档材料已准备，最终干净检出验收、最终提交/构建绑定和线上发布仍待完成。见[实验说明](EXPERIMENTS.zh-CN.md)、[发布说明](RELEASE_NOTES.md)、[候选资产](provenance/release-assets.json)和[路线图](ROADMAP.md)。EviWeave Controlled 属于后续工作，不在首版范围。

## 支持与发行范围

| 范围 | 支持或验收边界 |
|---|---|
| 完整沙箱流程 | Linux x86_64；需 Docker/dockerd、containerd、Bubblewrap 及必要 namespace/挂载权限；Docker 存储在项目内，不接系统 socket |
| 独立宿主档案 | CPython 3.12 / 3.13；测量并校验实际补丁版本、解释器字节、ABI、OS/内核及宿主工具 |
| 本地验收 | 同一物理宿主的 CPython 3.12.3 和 3.13.16；P5.2 每版源码 228 项、安装后 228 项测试通过 |
| 包元数据 | Python >=3.10、PyYAML>=6,<7；Python 3.10 实测与托管 CI 仍未验证；配置了 CI 不等于托管运行通过 |
| 其他平台 | macOS、Windows、ARM 和任意内核尚未验收 |
| Wheel | 22 个白名单业务模块、元数据和两个 CLI 别名：`eviweave`、`dataspace-baselines` |
| 源码检出 / sdist | 另含 `scripts/`、`tools/`、锁、合成样例及公开文档；下述流程需要这些内容 |

仅安装 wheel 不会取得仓库工具、数据或运行时。只需业务包的用户可在项目内 venv 中以 `python -m pip install /project/path/eviweave-0.1.0-py3-none-any.whl` 安装自行取得的 wheel，再使用 `eviweave --help`；复现使用下述锁定源码流程。不宣称已发布 PyPI。

两个原[精确档案](locks/README.md)登记的是 2026-10-23 核验的宿主，不是历史 V6 主机。公开独立重建具有独立的全树清单和身份。329 个系统包、21 个 Python 包和 TOOLS 相同不能证明历史 rootfs 相同。历史运行时导入仍是可选路径，须提供原清单；不提供私有 binary/rootfs 下载。

## 安装与资源准备

从源码根目录执行，采集和安装引导全程使用目标 CPython 3.12 或 3.13 作为 `python3`。初始准备仅需 Python 标准库和上述宿主工具。下载、临时文件、HOME、环境和 Docker/containerd 状态均须留在项目内。目标目录须全新且互不重叠；为制品、Docker vfs 层和导出 rootfs 预留空间。数据 ZIP 为 2,878,651,222 字节，解包内容合计 15,010,873,064 字节，另需运行时和构建空间。

以下命令下载公共资源并独立构建工作台，不调用模型。`workbench` 需要项目内 daemon 启动器所需的免密权限，不会安装缺少的宿主系统工具。

```sh
mkdir -p .audit/tmp .audit/cache .local/profiles
export TMPDIR="$PWD/.audit/tmp"
export XDG_CACHE_HOME="$PWD/.audit/cache"
export PYTHONDONTWRITEBYTECODE=1
python3 -B tools/public_resources.py codex --target .runtimes/public
python3 -B tools/public_resources.py workbench --target .runtimes/public/workbench \
  --storage .runtimes/public-build
python3 -B tools/public_resources.py evaluation --target .local/evaluation-v6-public60
python3 -B tools/host_profile.py collect --name public-host \
  --codex-root .runtimes/public/codex-0.145.0/platform-package \
  --workbench-root .runtimes/public/workbench --output .local/profiles/public-host.json
python3 -B tools/host_profile.py check --profile .local/profiles/public-host.json \
  --output .audit/public-profile-check
./scripts/install.sh --python "$PWD/.venv-experiment/bin/python" \
  --environment-profile .local/profiles/public-host.json
.venv-experiment/bin/eviweave --help
.venv-experiment/bin/dataspace-baselines --help
```

安装器先认证精确依赖 wheel 和内容，再执行锁定 pip 并安装包；引导解释器不需要预装 PyYAML。重复安装校验同一档案，不升级或覆盖冲突 venv。已有 `.local/config-v6/codex.yaml` 保留；全新安装会按所选运行时路径生成该文件，端点为 `https://example.invalid/v1`。

资源 URL、完整 revision、长度和摘要固定在[公共资源锁](provenance/public-resources-lock.json)。评估器固定 GitHub `6491caa4c70cc06cacb6103ba73cefb00746abfe`；数据/评分成员固定 Hugging Face `96be19257d7aa251dbce4771d46a26fc36f6dc52`。评分准备取得 120 个 ZIP 成员和评估器，并校验原 121 项锁，无需下载全部题目数据。完整数据另行准备：

```sh
python3 -B tools/public_resources.py dataset --target data/DataSpace-Benchmark
.venv-experiment/bin/python -B tools/standalone.py verify-evaluation \
  --evaluation-root .local/evaluation-v6-public60
```

完整 ZIP 解包前 SHA-256 必须为 `68f221fafcf0b50396eca55f8298af1ad73d586a6cac6995916afca535a560af`。数据根含 `input/task_N/task.json` 和 `input/task_N/context/`；`--dataset` 接收 `input/` 的父目录。已有完整数据可在核验后复用；工具拒绝冲突目标。不会搜索相邻 DataSpace 目录或读取其凭据。

`artifacts --cache ...` 可只取得 329 个精确 deb 和 21 个工作台 wheel 而不构建。复用缓存仍重算摘要；坏缓存被拒绝，不静默替换。旧 `libexpat1` 的固定摘要来自官方 HTTPS 观察，未取得其历史签名 Packages 条目；其余 328 项摘要来自已验证签名索引。此信任边界保留，不称为历史 Debian 快照认证通过。见[准备细节](provenance/public-preparation.md)。

构建后保留项目内 daemon 供镜像检查。使用 `python3 -B scripts/project_docker.py info --storage-root .runtimes/public-build` 查看；检查/运行后将 `info` 改为 `stop` 停止，后续镜像检查前以同一存储执行 `start`。不要替换成系统 daemon。

## 环境档案与私有配置

采集只写候选；采集/检查成功不会激活档案。显式 CLI 选择优先，其次是非空 `.local/env.sh`，再到继承环境变量，最后为原精确默认档案。Shell 包装器中，非空私有值覆盖继承值，空白值则继承。包装器首次使用时由[模板](configs/env.sh.example)创建私有文件。

全程使用一致设置：

| 设置 | 本文取值 |
|---|---|
| `EVIWEAVE_PYTHON` | `$PWD/.venv-experiment/bin/python` |
| `EVIWEAVE_ENVIRONMENT_PROFILE` | `$PWD/.local/profiles/public-host.json` |
| `EVIWEAVE_CONFIG` | `$PWD/.local/config-v6/codex.yaml` |
| `EVIWEAVE_DATASET` | `$PWD/data/DataSpace-Benchmark` |
| `EVIWEAVE_EVALUATION_ROOT` | `$PWD/.local/evaluation-v6-public60` |
| `EVIWEAVE_API_KEY` | 用户在终端设置专用 key；私有模板保持空白以继承 |

使用前检查已有私有设置。`EVIWEAVE_BOOTSTRAP_PYTHON` 选择安装引导解释器，不代表目标 venv。`EVIWEAVE_AUDIT_ROOT` 可将准备/检查诊断放到项目内指定目录。统一包装器没有 `EVIWEAVE_BASE_URL` 或 `EVIWEAVE_DATASET_ROOT` 设置；端点写 YAML，其他设置使用上表名称。

已有历史配置而准备了新的公开运行时时，生成**全新**离线文件，检查运行时路径后显式选择；不会覆盖 `codex.yaml`：

```sh
.venv-experiment/bin/python -B tools/host_profile.py config \
  --profile .local/profiles/public-host.json --output .local/config-v6/offline.yaml
```

## 无模型检查与合成执行

本节无需 API key。离线 doctor 使用生成的占位配置；检查本地运行时内容和能力，不认证服务。每次独立检查使用全新审计输出。

```sh
.venv-experiment/bin/python -B tools/host_profile.py doctor \
  --profile .local/profiles/public-host.json --config .local/config-v6/offline.yaml \
  --output .audit/public-offline-doctor
.venv-experiment/bin/python -B tools/unified_smoke.py \
  --environment-profile .local/profiles/public-host.json \
  --evaluation-root .local/evaluation-v6-public60 --output-root '.audit/public synthetic'
.venv-experiment/bin/python -B tools/run_regression.py \
  --suite unit --output-root .audit/public-unit
```

合成入口强制替换成本地执行器并额外禁网，检查 CSV 字节、归集、中断/续跑及完成后续跑零新增尝试；2/2 是人工测试分数。不执行 benchmark 推理或真实模型客户端。包内另有 `eviweave doctor --offline --config ...`；它与档案 doctor 的区别是不会选择/检查独立宿主档案。普通 doctor 要求 key，两种模式都不认证服务。

## 统一实验流程

**本节 `run` 命令会调用真实模型并产生费用。** 以下仅为使用示例，文档整理不执行模型运行。先准备数据/运行时/评分材料、选用检查通过的档案，再由用户自行设置专用 `EVIWEAVE_API_KEY`。不要把 key 写入 YAML、输入数据、命令参数或日志。不支持回退到其他 key。

真实使用时，在私有 `.local/config-v6/codex.yaml` 中将 `experiment.model.openai_base_url` 设置为兼容 V6 的 HTTPS Responses 基础端点，不带内嵌凭据、query 或 fragment。保留 `qwen3.8-flash`、Responses、relation-v6/simple-fixes-v1、32,768 输出 token、983,616 上下文 token、单题 1,800 秒和并发 1。运行时路径须与所选档案一致。新端点/模型/宿主构成新的环境记录，不是历史模型身份的证明。

检查 `.local/env.sh` 后，先对单题进行无推理检查。必须存在 key；成功仍报告 `service_auth=NOT_TESTED`、`model_calls=0`。其他前置条件通过但缺 key 时返回 2 和 `MISSING_KEY`。此生产路径拒绝占位端点。

```sh
./scripts/experiment.sh check --tasks 33 \
  --config .local/config-v6/codex.yaml --dataset data/DataSpace-Benchmark \
  --evaluation-root .local/evaluation-v6-public60 \
  --environment-profile .local/profiles/public-host.json
```

真实单题、公开批次及该批次续跑：

```sh
./scripts/experiment.sh run --tasks 33 --output .audit/task33-v6 \
  --config .local/config-v6/codex.yaml --dataset data/DataSpace-Benchmark \
  --evaluation-root .local/evaluation-v6-public60 \
  --environment-profile .local/profiles/public-host.json
./scripts/experiment.sh run --scope public60 --output .audit/public60-v6 \
  --config .local/config-v6/codex.yaml --dataset data/DataSpace-Benchmark \
  --evaluation-root .local/evaluation-v6-public60 \
  --environment-profile .local/profiles/public-host.json
./scripts/experiment.sh run --output .audit/public60-v6 --resume --yes
```

省略 `--tasks`/`--scope` 时默认 **public60**，`--repeat 1`。`--tasks 23 35` 显式选取 1–410 范围内的题；`--scope all` 选择全部 410 题，费用可能显著增加。`--repeat N` 计划 N 轮，不按最好一次选答案。不要用未限定范围的批次测试安装。

续跑恢复已保存的题目/轮数/配置/数据/档案，拒绝显式替换，包括 `--environment-profile`；不会导入外部档案漂移。原配置、已登记输入、源码/环境身份和运行时检查继续有效。源码改变后可能需要原冻结工具检出或新实验；不得编辑旧元数据绕过身份。`--yes` 明确允许无交互复用。

每个新分配的尝试都会替换本题当前答案，失败也一样。续跑对未启动、中断、执行失败或原始预测损坏的项目每次最多增加一次；正常提交但答案错误的项目跳过。仅归集副本损坏时可无模型修复。Ctrl+C 取消活动进程组并记录中断（130）。已完成实验续跑零新增尝试。

当前任务返回后，确认最终 401/402/403 失败即停止派发并取消在途。保留冻结客户端重连行为，不是逐 HTTP 即时熔断。429/500 为普通任务失败。见[修复边界](provenance/bugfix.zh-CN.md)。

## 离线验证与历史证据

统一验证读取已保存的配置/输入摘要/档案和实验内独立 121 项评分副本，无需 key、原数据/配置或模型运行时，拒绝并发写入。输出带版本的 `summary.json`、`score.json`、`summary.csv`，共享报告 ID。`submitted` 代表执行和 CSV 合法，不代表答案正确。准确率按不同公开题号计数，包含缺预测；all410 中 60 题有评分、350 题未评分。

```sh
./scripts/experiment.sh verify --experiment .audit/public60-v6 \
  --output .audit/public60-v6-verification
```

历史证据 ZIP 和外部 `.sha256` 是**待发布 Release 资产，尚未上传**。取得后将两者放入 `.local/evidence-v6/`。先验证再解包；验证器只需标准库和源码工具。随后可用 CPython 3.12.3 对已有预测离线评分：

```sh
python3 -B tools/package_evidence.py verify \
  --archive .local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip \
  --output-root .audit/evidence-verification
unzip -q .local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip -d .local/evidence-v6/extracted
.venv-experiment/bin/python -B tools/standalone.py score \
  --evaluation-root .local/evaluation-v6-public60 \
  --prediction-root .local/evidence-v6/extracted/predictions \
  --output-root .audit/historical-score
```

预期历史离线结果为 **45/60**、15 失败、350 未评分；首轮历史记录为 **44/60**。它们与历史官方 **333/410、81.22%** 分开：官方评测 2026-10-21，核验 2026-10-22，本次复核 2026-10-24；复核时页面仍列为并列第一，这是带日期的观察，不保证实时排名。见[官方榜单](https://dataspace-bench.github.io/#leaderboard)和[实验边界](EXPERIMENTS.zh-CN.md)。

ZIP 含 410 份原字节预测、414 条尝试摘要、固定选择和 5 题 / 6 次尝试的**精选轨迹**。不含完整轨迹、参考答案、私有运行时和脱敏映射。三次缺失用量保留未知，已记录 token 不能证明完整费用。校验和证明字节完整性，不是作者签名。

## 离线费用分析

完整在线采集仍为 **UNSUPPORTED_G1**：固定客户端遥测缺失用量/金额扩展，且未证明每次请求前持久化。启用观测档案会在模型启动前拒绝。可执行统一实验 v2 尚未实现；分析 v2 是离线容器，不是 run/resume 格式。

由用户显式提供项目内脱敏证据和已审阅定价。[定价模板](configs/pricing.example.json)费率为 null，不是报价。导入不连接服务或账户控制台。

```sh
./scripts/experiment.sh cost import --source .local/import/calls.json \
  --source-type calls --pricing .local/pricing/reviewed.json --output .audit/call-analysis
./scripts/experiment.sh verify --experiment .audit/call-analysis
./scripts/experiment.sh cost revalue --experiment .audit/call-analysis \
  --pricing .local/pricing/reviewed.json --output .audit/call-revaluation
./scripts/experiment.sh cost submission-check --experiment .audit/call-analysis
```

请求枚举不完整时，`submission-check` 应失败：观测请求数是下界，`cost_complete=false`；导入不能认证总请求数或正式提交总费用。服务商返回、公开价计算与实际支付是三条独立金额路径。同响应字节但物理请求 ID 不同会分别计数；同一证据重复导入保持幂等。Token 子字段不加入父字段总量，未知值不补零。

新分析使用 `schema_version=2`、`import_identity_version=2`，请求合同仍为 v1。旧分析 v1 只读且须已有锁；验证/重估必须指定全新独立输出。旧报告不会自动重算：

```sh
./scripts/experiment.sh verify --experiment .audit/old-v1-analysis \
  --output .audit/old-v1-verification-new
./scripts/experiment.sh cost revalue --experiment .audit/old-v1-analysis \
  --pricing .local/pricing/reviewed.json --output .audit/old-v1-revaluation-new
```

新计算登记 `calculation_policy_version=decimal-quantum-v2`：每个物理请求先合计组件，再按精确步长（含 0.05）舍入一次，最后跨请求汇总。舍入前已丢失精度时拒绝计价。见[合同、JSON/SSE/账单导入与旧用量快照](provenance/observability.zh-CN.md)及[舍入政策](provenance/config-rounding.zh-CN.md)。真实服务字段和账户账单仍未验收。

## 故障排查

| 现象 | 处理或含义 |
|---|---|
| 不支持的 OS、架构或 ABI | 完整流程使用 Linux x86_64 / CPython 3.12 或 3.13，不放宽锁 |
| 缺运行时/评分/数据 | 按上述显式目标准备，验证 121 项评分文件；`--dataset` 指向含 `input/` 的目录 |
| 档案/解释器/内容不匹配 | 重新采集、检查、显式选择独立档案和全新 `.venv*`；保留原精确锁，不混用 venv 或实验档案 |
| 安装后运行时路径仍旧 | 已有 YAML 按设计保留；生成全新离线配置并显式选择 |
| namespace、挂载或 daemon 权限不足 | 提供所需宿主权限和项目内 daemon；不回退到宿主直接执行 |
| 摘要不匹配或缓存/目标冲突 | 保留失败收据；检查来源并选择全新项目内缓存/目标，不覆盖历史资源 |
| `MISSING_KEY` 或占位端点 | 只设置专用 key 和有效私有 Responses URL；无 key 准备使用离线 doctor |
| 最终 401/402/403 | 当前任务确认失败后停批并取消在途；修复访问/账户额度后再有意续跑 |
| 429/500 | 普通执行失败，检查证据；不自动扩题或增加重试 |
| `no_prediction` / 缺原始 CSV | 属执行失败，保留日志并有意续跑；正常提交但答案错不同，resume 不重试 |
| 完整性冲突 | 验证失败，记录 `attempt_integrity` 并排除冲突用量；不把原始记录修成成功 |
| 锁占用 | 等待写入者结束；不对同目录并发 verify/revalue/run |
| 旧实验源码不匹配 | 使用其原冻结工具版本或新建实验；文档不授权绕过检查 |
| 旧费用报告 / v1 分析 | 以审阅定价重估到全新输出；保留旧字节和源目录已有锁 |

退出 0 表示请求的操作完成，不代表全部答案通过；1 为执行/证据/评分/报告失败，2 为参数或前置条件失败，130 为中断。离线 verify 可成功报告已有执行失败，不重试任务。

## 更多资料与归属

[数据格式与自定义单题](DATA_FORMAT.md)（中文；统一流程未注册自定义评分）、[合成示例](examples/synthetic/README.md)、[源码来源](provenance/README.zh-CN.md)、[发布准备与来源](provenance/p5-documentation.md)、[CITATION.cff](CITATION.cff)。

另保留 `scripts/check_task33.sh` 终端工具；`--check-only` 检查本地输入/key 可读性，不验证鉴权。它使用 `tools/standalone.py configure` 生成的 `.local/config-v6/settings.json`，默认 `.venv/bin/python`，与统一流程不同；上述流程无需此工具。创建独立配置需要全新输出目录，不覆盖统一配置。实际使用会串行启动固定三次付费尝试，HOME/会话独立；环境/鉴权/缺预测失败停止，答案错误继续。不要将它作为安装检查启动。

代码和文档采用 [MIT](LICENSE)，保留 DataSpace Authors 声明及 **袁珺 / Jun Yuan** 的 EviWeave 署名。业务源码仅来自历史冻结 V6 快照，不取当前 DataSpace 代码。DataSpace 数据、预测及第三方运行时组件保留各自来源条款；本项目 MIT 不为其重新授权。数据取自[官方发布](https://huggingface.co/datasets/HKUSTDial/DataSpace/tree/96be19257d7aa251dbce4771d46a26fc36f6dc52/release)，并参阅[上游](https://github.com/HKUSTDial/DataSpace)。私有 `docs/`、审计、数据和运行时目录不进入 Git 或发行包。
