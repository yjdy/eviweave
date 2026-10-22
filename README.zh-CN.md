# EviWeave — Evidence-Guided Data Agents

作者：**袁珺 / Jun Yuan**（[yjdy](https://github.com/yjdy)）。从 DataSpace 历史冻结版本提取 `relation-v6 + simple-fixes-v1 + Codex`，首版包版本 `0.1.0`，内部包名保留 `dataspace_baselines`。

[English](README.md) · [来源](provenance/README.zh-CN.md)

P0 基线核验和 P1 独立提取已通过；P2 提供历史运行时移植、锁定版本工作台构建、离线检查和合成沙箱执行。P3 提供离线行为回归、冻结参考对照和历史沙箱检查；P4 提供本地实验附件整理、精选轨迹脱敏和冻结离线评分；公开发布留在 P5。正式技术报告延后。

## 官方评估

EviWeave V6 在 DataSpace 官方全量评估中答对 **333/410 题，Task Accuracy 为 81.22%**。截至 **2026-10-22**，与 Jitto Build + Claude Opus 5.5 **并列第一**，见[官方榜单](https://dataspace-bench.github.io/#leaderboard)。

| 项目 | 官方记录 |
|---|---|
| 登记名称 | `codex-controlled + Qwen3.8-Flash` |
| 官方评测日期 | 2026-10-21 |
| 评测范围 | DataSpace 全量 410 题 |
| 条目 ID | `yjdy-codex-controlled-qwen38-flash` |

该历史条目对应本项目的 V6 基线：Codex CLI 0.145.0、`relation-v6` 和 `simple-fixes-v1`。登记名称早于 EviWeave 命名，后续 EviWeave Controlled 控制器实现不在本包内。提交保留全部 **414 次尝试**，其中任务 **22、23、207、273** 各有一次人工触发的恢复尝试；四题固定采用 `run_2`，其余 406 题采用 `run_1`，不按分数重新选择。

独立的本地公开子集结果为**首轮 44/60、恢复后 45/60**，来源和选择政策见[来源说明](provenance/README.zh-CN.md)。官方榜单已于 2026-10-22 重新核对；以上为历史提交成绩，本次整理未重新推理或重跑评测。

## 安装与检查

需要 Python 3.10+，唯一 Python 运行依赖为 `PyYAML>=6,<7`。在项目根目录安装，也可安装 `dist/` 内构建的 wheel：

```sh
mkdir -p .audit/tmp .audit/cache
export TMPDIR="$PWD/.audit/tmp"
export PIP_CACHE_DIR="$PWD/.audit/cache/pip"
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/eviweave --version
.venv/bin/eviweave list
.venv/bin/eviweave run --help
.venv/bin/eviweave batch --help
```

系统缺少 `ensurepip` 时，可使用已安装的 uv：`UV_CACHE_DIR="$PWD/.audit/cache/uv" uv --offline venv --python python3 .venv`，然后执行 `UV_CACHE_DIR="$PWD/.audit/cache/uv" uv pip install --python .venv/bin/python .`。

配置模板为 `configs/codex.example.yaml`。其中端点为不可用占位值，实际配置使用与历史V6一致的Responses端点和模型 `qwen3.8-flash`。用户须自行设置环境变量 `EVIWEAVE_API_KEY`，使用EviWeave专用API key；不将密钥写入YAML，不读取DataSpace的api.env，也不回退到DASHSCOPE_API_KEY或OPENAI_API_KEY。下文独立配置工具可生成私有配置。工作台和Codex运行时宿主路径均相对配置文件解析，支持空格和runtime_path的 `{version}` 占位符。

```sh
.venv/bin/eviweave doctor --config configs/codex.example.yaml
```

`doctor` 不调用模型；凭据、Docker、Bubblewrap、Data Workbench 1.0 或 Codex CLI 0.145.0 缺失时返回非零并给出诊断。`run`、`batch` 默认使用 Codex，也接受显式 `--harness codex`，其他代理明确拒绝；兼容 `dataspace-baselines` 入口。实际执行 run/batch 需要完整环境，可能产生推理费用，本轮未执行此类实验。

## 运行时准备与离线合成验收

需要 Linux x86_64、GNU `cp`/`tar`、Docker、Bubblewrap 和 Python。准备脚本与人工合成样例进入源码发行包；wheel 仅包含 Python 包，因此请保留仓库或源码包。

```sh
scripts/install_codex_runtime.sh --help
scripts/install_codex_runtime.sh
python3 -B scripts/project_docker.py start
scripts/prepare_workbench.sh build --docker-host "unix://$PWD/.runtimes/build/docker.sock"
scripts/prepare_workbench.sh export --docker-host "unix://$PWD/.runtimes/build/docker.sock" \
  --runtime-root .runtimes/data-workbench-rebuilt
```

Codex 安装器只下载 `0.145.0`，解包前核验 npm SHA-512；也支持 `--from-dir` 加 `--manifest` 导入，或 `--from-archive` 加 `--sha256`。运行时、缓存与临时目录默认均在项目内，可在项目内自定义。重复导入复用已核验副本，内容冲突时失败且不覆盖。准备过程仅执行真实 `codex --version`。

独立 Docker 和 containerd 的持久数据均位于项目内，需要已有 `sudo -n` 权限。构建沿用历史基础镜像摘要，并锁定全部记录的 Python/系统包版本；无法获取锁定版本时失败，不自动升级。重建标签为 `eviweave-workbench:p2-rebuilt`。使用重建导出时，需相应修改配置中的工作台 image 和 rootfs，doctor 通过 `DOCKER_HOST="unix://$PWD/.runtimes/build/docker.sock"` 选择本地实例。使用完执行 `python3 -B scripts/project_docker.py stop`。准备脚本拒绝向系统 daemon 构建或导出。

历史移植与重建分别记录。取得独立的历史运行时和可信文件清单后，使用 `prepare_workbench.sh import --from-dir ... --manifest ...`；归档导入要求 `--from-archive`、`--sha256` 及 `--manifest`。可通过 `python3 -B scripts/runtime_support.py --root ... --output .audit/runtime-files.json` 采集现存目录清单，但新采集不等于历史认证。`prepare_workbench.sh verify` 逐项验证。历史 binary/rootfs 不随代码发行，目前尚无公开附件下载入口，身份与重建差异见[运行时来源说明](provenance/runtime-changes.zh-CN.md)。

示例配置指向已核验的历史运行时副本后：

```sh
.venv/bin/eviweave doctor --offline --config configs/codex.example.yaml
.venv/bin/python -B tools/smoke_runtime.py --config configs/codex.example.yaml \
  --output-root .audit/synthetic-smoke
```

输出目录必须全新。离线 doctor 将凭据显示为 `[skip]`，其他必要检查失败仍返回非零；默认 doctor 继续在缺少凭据时失败。两种模式都不认证服务、不发送推理请求。检查实际包版本、完整文件清单、英/中文 OCR 数据、只读资料/运行时/导出器挂载及可写输出。

合成工具只接受占位端点，子进程仅提供无效测试 key，额外禁用网络，通过原始 Bubblewrap、运行器、导出器和批次链路运行确定性的本地 Python 程序，验证单题、两题归集、导出字节摘要及续跑不新增尝试。预期 CSV 不挂载到沙箱。这证明离线链路可用，不代表真实 Codex 推理或官方成绩复现。

离线验收及构建（构建工具需安装在本地环境内）：

```sh
mkdir -p .audit/tmp
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m unittest discover -s tests -v
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m build
```

## 离线行为回归

在保留源码检出或源码发行包、并已安装 Python 包的环境中执行：

```sh
.venv/bin/python -B tools/run_regression.py --suite unit --output-root .audit/unit
.venv/bin/python -B tools/run_regression.py --suite all --output-root .audit/regression \
  --frozen-source /path/to/frozen_src --config configs/codex.example.yaml
```

输出目录必须全新或为空。unit 不依赖历史材料；reference 必须显式提供冻结来源，使用独立子进程并各自断言人工预期；runtime 必须有已核验历史运行时和占位端点配置，缺材料直接失败。见[测试来源及证据边界](provenance/regression.zh-CN.md)。

原始外层检查不附加禁网，只观察网络命名空间，不发起外部连接。独立的固定 CLI config/read 诊断无推理地比较初始/trusted 配置；P2 合成诊断另加禁网。真实 Codex 内层命令执行仍未验证。轻量 CI 配置 Python 3.10/3.12/3.13，托管执行留首次公开推送时确认。在项目内环境安装 build、pip 后，可用 `python -B tools/ci_check.py --output-root .audit/ci-result` 执行同一套本地构建安装及 unit 检查。

`tools/verify_baseline.py` 使用标准库，依次接收历史实验目录、现有 benchmark 目录、私有输出目录；四线程核对摘要，缺失、变更、无法解析或证据不一致均失败。新增输出必须放在本项目内，不能指向只读来源。数据、预测、凭据和私有原始审计不随代码发行。

代码及说明文档采用 MIT，保留 DataSpace 上游声明，见 [LICENSE](LICENSE)。逐文件来源和改动记录保存在 `provenance/`。数据集按原发布条款另行获取。

## 历史实验附件

从源码检出或源码包运行，正式冻结评分使用 Python 3.12.3：

```sh
python3 -B tools/package_evidence.py build \
  --experiment-root /path/to/historical-experiment \
  --baseline-report /path/to/private-P0-result.json \
  --official-evidence-root /path/to/verified-official-snapshot \
  --output-root .audit/evidence-build
python3 -B tools/package_evidence.py verify \
  --archive .audit/evidence-build/eviweave-v0.1.0-v6-evidence.zip \
  --output-root .audit/evidence-check
```

输出目录须为项目内全新或空目录。附件含410份原字节预测、414条摘要和33、328、35、391、23的**精选轨迹**（23含失败与恢复）；完整原始trace私下保留。仅复制冻结评估器，对既有预测离线重评45/60；首轮44/60作为历史记录，没有新推理或benchmark任务执行。工具独立扫描残留并拒绝缺失/变更材料；verify只用标准库，不需要历史来源、安装包或runtime。来源和附件不进入Git/wheel/sdist；当前未上传附件，线上发布留P5。详见[来源和证据边界](provenance/evidence.zh-CN.md)。

## 独立配置、数据下载与评分

完整数据由[官方Hugging Face发布库](https://huggingface.co/datasets/HKUSTDial/DataSpace)单独提供，下载方式参见[上游README](https://github.com/HKUSTDial/DataSpace#dataset)。下载器是可选准备工具，不是EviWeave运行依赖。所有下载、缓存和解包都放在本项目内；下载后先校验官方SHA-256。为可重复获取，请将 `DATA_REVISION` 设为发布库中选择的完整提交ID并保留该值。

```sh
mkdir -p .local/download .audit/tmp .audit/cache
export TMPDIR="$PWD/.audit/tmp"
export PIP_CACHE_DIR="$PWD/.audit/cache/pip"
export HF_HOME="$PWD/.local/hf-cache"
python3 -m venv .local/download-env
.local/download-env/bin/python -m pip install huggingface_hub
export DATA_REVISION='<完整的Hugging Face发布提交ID>'
.local/download-env/bin/hf download HKUSTDial/DataSpace \
  release/DataSpace-Benchmark.zip release/DataSpace-Benchmark.zip.sha256 \
  --repo-type dataset --revision "$DATA_REVISION" --local-dir "$PWD/.local/download"
(cd .local/download/release && sha256sum --check DataSpace-Benchmark.zip.sha256)
unzip .local/download/release/DataSpace-Benchmark.zip -d data
```

数据路径必须显式配置，根目录含 `input/task_N/task.json` 和 `context/`。工具不会寻找相邻仓库或自动回退到其他数据源。本机当前私有设置使用项目内的 `data/DataSpace-Benchmark/`，不再指向相邻DataSpace项目；更换数据目录时重新生成配置到全新输出目录即可。`data/` 不进入Git或发行包。

```sh
export EVIWEAVE_DATASET_ROOT="$PWD/data/DataSpace-Benchmark"
export EVIWEAVE_BASE_URL='<与V6一致的Responses端点>'
# 用户自行设置，勿提交或写入配置文件：
export EVIWEAVE_API_KEY='<你的EviWeave专用API key>'
.venv/bin/python -B tools/standalone.py configure \
  --dataset-root "$EVIWEAVE_DATASET_ROOT" --base-url "$EVIWEAVE_BASE_URL" \
  --model qwen3.8-flash --output-root .local/config
# 以下会调用真实模型，仅在计划运行该题时执行：
.venv/bin/python -B tools/standalone.py run --settings .local/config/settings.json \
  --task 33 --output-root .audit/independent-task33
```

本机已验证的冻结评分副本位于 `.local/evaluation-v6-public60/`，必要历史证据独立保存在 `.local/evidence-v6/`；这些私有资源不随代码发行。在其他机器需自行提供符合 [121项冻结摘要锁](provenance/evaluation-lock.json) 的评分材料，通过显式路径导入。来源必须含 `evaluate.py`、`configs/` 和 `output/`，既可来自历史冻结评分包，也可自行整理官方下载的公开材料，摘要不符则拒绝；不自动从当前上游评估器替换冻结版本。

```sh
python3 -B tools/standalone.py import-evaluation \
  --source /path/to/frozen-evaluation --output-root .local/evaluation-v6-public60
python3 -B tools/standalone.py verify-evaluation --evaluation-root .local/evaluation-v6-public60
python3 -B tools/standalone.py score --evaluation-root .local/evaluation-v6-public60 \
  --prediction-root /path/to/predictions --output-root .audit/independent-score
# 仅评一道公开题时显式添加 --task 33；正式历史复核使用Python 3.12.3。
```

安装build和pip的Python可执行独立隔离验收：

```sh
python3 -B tools/verify_isolation.py --block-path /absolute/path/to/DataSpace \
  --evaluation-root .local/evaluation-v6-public60 \
  --archive .local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip \
  --config configs/codex.example.yaml --output-root .audit/isolation-check
```

工具用系统挂载和PID命名空间使指定目录不可读取，不改动原目录；无真实凭据，执行构建、独立安装、测试、offline doctor、合成沙箱任务及导出、合成评分和历史附件评分。合成执行不调用真实模型；真实凭据和真实数据的独立运行需单独验收。工具和测试进入源码包，`.local/`、数据、答案、历史附件及审计全部排除。详见[隔离来源与边界](provenance/isolation.zh-CN.md)。

### 第33题三次终端验证

在已 `export EVIWEAVE_API_KEY` 的终端执行。默认读取 `.local/config-v6/settings.json` 和 `.local/evaluation-v6-public60/`，使用 `.venv/bin/python` 与已核验冻结源码；可用 `EVIWEAVE_PYTHON` 指定其他含PyYAML的解释器。脚本从自身位置定位项目，可从任意目录启动。

```sh
# 可选：只检查key可读性、输入及本地运行环境，无模型调用，不验证服务端鉴权。
./scripts/check_task33.sh --check-only
# 实际调用模型：串行跑33题三次，每次独立会话/HOME，沿用V6的30分钟单题时限。
./scripts/check_task33.sh
```

会产生推理费用，三次任务时限合计最多90分钟，另加检查及评分时间。每次启动创建新的 `.audit/task33-three-runs-*/`；三次原始日志、预测、用量、单题冻结评分及 `summary.json`/`summary.csv` 均私下保留，不覆盖旧结果。输入、源码、配置和评分材料在运行前后核对摘要。脚本不主动输出或存储key值，也不向模型提供历史答案或评分反馈。环境/鉴权/缺预测失败会停止；答案未通过会继续完成既定三次。Ctrl+C取消当前任务，不启动后续任务。

汇总分别记录环境变量是否可读、是否记录到模型完成事件、正确次数、行数和预测SHA-256。退出码0表示三次完整执行并评分（或check-only通过），不表示答案通过；基础设施失败返回非零。即使观测到预测差异，也不能仅凭三次证明是纯随机模型波动，模型别名背后的版本和服务端采样尚未锁定。
