# EviWeave — Evidence-Guided Data Agents

作者：**袁珺 / Jun Yuan**（[yjdy](https://github.com/yjdy)）。从 DataSpace 历史冻结版本提取 `relation-v6 + simple-fixes-v1 + Codex`，首版包版本 `0.1.0`，内部包名保留 `dataspace_baselines`。

[English](README.md) · [来源](provenance/README.zh-CN.md)

P0 基线核验和 P1 独立提取已通过；P2 提供历史运行时移植、锁定版本工作台构建、离线检查和合成沙箱执行。P3 提供离线行为回归、冻结参考对照和历史沙箱检查；P4–P5 实验附件及公开发布尚未实施。正式技术报告延后。

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

配置模板为 `configs/codex.example.yaml`。其中端点为不可用占位值，需要自行填写兼容 Responses 的端点，通过环境变量 `EVIWEAVE_API_KEY` 提供凭据，不将密钥写入 YAML。工作台和 Codex 运行时宿主路径均相对配置文件解析，支持空格和 runtime_path 的 `{version}` 占位符。Codex 不使用的 `anthropic_base_url` 可省略，提供时保留原值。

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
