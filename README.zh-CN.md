# EviWeave — Evidence-Guided Data Agents

作者：**袁珺 / Jun Yuan**（[yjdy](https://github.com/yjdy)）。从 DataSpace 历史冻结版本提取 `relation-v6 + simple-fixes-v1 + Codex`，首版包版本 `0.1.0`，内部包名保留 `dataspace_baselines`。

[English](README.md) · [来源](provenance/README.zh-CN.md)

本轮仅实施 P0 基线核验和 P1 独立代码提取。完整运行环境、合成任务贯通、行为回归、实验附件和公开发布留在 P2–P5；当前不代表正式开源发布完成。正式技术报告延后。

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
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/eviweave --version
.venv/bin/eviweave list
.venv/bin/eviweave run --help
.venv/bin/eviweave batch --help
```

系统缺少 `ensurepip` 时，可使用已安装的 uv：`uv venv .venv`，然后执行 `uv pip install --python .venv/bin/python .`。

配置模板为 `configs/codex.example.yaml`。其中端点为不可用占位值，需要自行填写兼容 Responses 的端点，通过环境变量 `EVIWEAVE_API_KEY` 提供凭据，不将密钥写入 YAML。工作台和 Codex 运行时宿主路径均相对配置文件解析，支持空格和 runtime_path 的 `{version}` 占位符。Codex 不使用的 `anthropic_base_url` 可省略，提供时保留原值。

```sh
.venv/bin/eviweave doctor --config configs/codex.example.yaml
```

`doctor` 不调用模型；凭据、Docker、Bubblewrap、Data Workbench 1.0 或 Codex CLI 0.145.0 缺失时返回非零并给出诊断。运行时安装与合成任务在 P2 完成。`run`、`batch` 默认使用 Codex，也接受显式 `--harness codex`，其他代理明确拒绝；兼容 `dataspace-baselines` 入口。实际执行 run/batch 需要完整环境，可能产生推理费用，本轮未执行此类实验。

离线验收及构建（构建工具需安装在本地环境内）：

```sh
mkdir -p .audit/tmp
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m unittest discover -s tests -v
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m build
```

`tools/verify_baseline.py` 使用标准库，依次接收历史实验目录、现有 benchmark 目录、私有输出目录；四线程核对摘要，缺失、变更、无法解析或证据不一致均失败。新增输出必须放在本项目内，不能指向只读来源。数据、预测、凭据和私有原始审计不随代码发行。

代码及说明文档采用 MIT，保留 DataSpace 上游声明，见 [LICENSE](LICENSE)。逐文件来源和改动记录保存在 `provenance/`。数据集按原发布条款另行获取。
