# Preparation scripts / 准备脚本

Author / 作者：袁珺 / Jun Yuan. Code and documentation: MIT, retaining DataSpace attribution.

## Status / 状态

These three entries exist on the development branch. Hosted acceptance has not
run yet. They are not part of the original v0.1.0 tag or released packages. No
new model inference, benchmark run, online cost collection, or publication is
performed by these entries.

三个入口已写入开发分支，托管验收尚未执行；它们不在原 v0.1.0 标签或发行包中。
不执行真实推理、benchmark 运行、在线费用采集或发布。

## Entries / 入口

| Entry | Defaults and contract / 默认值与合同 |
|---|---|
| `scripts/prepare_environment.sh` | CPython 3.12/3.13, Linux x86_64; `.venv-experiment`; profile `public-host`; runtime `.runtimes/prepared`; offline doctor only. / 仅离线 doctor，不准备评分或题目数据。 |
| `scripts/prepare_dataspace.sh` | `--mode evaluation`; `.local/evaluation-v6-public60`; full data only with `--mode full`. / 默认评分材料，完整数据须显式选择。 |
| `scripts/verify_history.sh` | CPython 3.12.3; `.local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip`; network only with `--download`. / 默认离线，缺外部摘要失败。 |

Use `--python` to choose an already available interpreter; no entry installs
host Python or system tools. `EVIWEAVE_BOOTSTRAP_PYTHON` is an optional shell
bootstrap selector. The entries do not source `.local/env.sh`. They launch an
isolated Python process with a credential-free environment. The shared module
uses only the standard library before the locked installer supplies package
dependencies.

`--python` 选择已有解释器，不安装宿主 Python 或系统工具。
`EVIWEAVE_BOOTSTRAP_PYTHON` 可选择入口引导解释器。入口不加载 `.local/env.sh`，
使用不传递凭据的环境启动隔离 Python；锁定安装前共享模块只依赖标准库。

Every entry supports `--help` and `--check-only`. Static preflight performs no
network requests, writes, service launches or capability probes. A static pass
is not proof of sandbox permission, available remote resources, service
authentication or a usable runtime. Actual preparation performs required
capability checks and fails without a host-execution fallback.

三个入口均支持 `--help` 和 `--check-only`。静态前置检查不联网、不写入、不启动
服务、不执行能力探针；通过不证明沙箱权限、远程资源、服务鉴权或运行时可用。
实际准备仍执行所需能力检查，失败不回退宿主直接执行。

## Paths, reuse and storage / 路径、复用与存储

All paths are relative to the project root unless absolute. Sources and outputs
must stay within project private storage, be disjoint, and not traverse symbolic
links. Venv directories must be project-local `.venv*`; normal interpreter
symlinks inside them remain supported. Preparation uses an exclusive project
lock; contention fails immediately. Check-only does not create/open a lock.

相对路径以项目根为基准；输入、输出须在项目私有目录内且互不重叠，拒绝符号链接
写穿。venv 目录须为项目内 `.venv*`，其正常解释器链接仍支持。实际准备使用项目
互斥锁，占用时立即失败；静态检查不创建或打开锁。

Existing targets require explicit `--reuse`. Downloads always authenticate
cached bytes and reject corruption. Full dataset reuse requires its original
authenticated ZIP retained in the selected cache, then compares the complete
file set and per-file SHA-256; a missing ZIP does not trigger a download. Failed
preparation does not delete old data or silently replace targets.

已有目标需显式 `--reuse`，缓存每次重验摘要，损坏拒绝。完整数据复用需要所选
缓存中保留已认证的原 ZIP，再比较完整文件集合和逐文件 SHA-256；缺 ZIP 不自动
下载。不删除旧数据或静默覆盖目标。

Environment preflight requires at least 8GiB free, without promising that this
is sufficient for every host. Full data preflight accounts for the ZIP, retained
range parts, 15,010,873,064 extracted bytes and margin. Cache and archive cleanup
is never automatic.

环境初检要求至少 8GiB 可用空间，不承诺覆盖所有宿主峰值。完整数据检查包含 ZIP、
保留的 Range 分块、15,010,873,064 字节解压内容和余量，不自动清理缓存或归档。

The independent runtime has a new host identity. Original exact profiles,
dependency/resource/evaluation locks, production configuration and 22/17 frozen
files stay unchanged. The old libexpat1 artifact retains the trust boundary
documented in [public preparation](public-preparation.md).

独立重建登记新宿主身份；原精确档案、依赖/资源/评分锁、真实配置和 22/17 冻结
文件不改。旧 libexpat1 的信任边界继续遵循[公共准备说明](public-preparation.md)。

## Environment handoff / 环境衔接

Successful preparation records a profile without activating it or editing
`.local/env.sh`. Select `.local/profiles/public-host.json` explicitly for later
experiments. Existing `.local/config-v6/codex.yaml` stays unchanged; the separate
`offline-public-host.yaml` uses a placeholder endpoint and a recorded checksum.
Edited or unrecorded existing offline configuration is rejected on reuse.

准备成功只登记档案，不自动激活或改写 `.local/env.sh`。后续显式选择
`.local/profiles/public-host.json`。已有 `.local/config-v6/codex.yaml` 保留；
独立 `offline-public-host.yaml` 使用占位端点并登记摘要，复用时拒绝修改过或无
摘要登记的文件。

The script stops only daemons it started. For later image checks or the shipped
synthetic integration, restart the same storage, then stop it when finished:

脚本只停止本次启动的 daemon。后续镜像检查或合成测试使用同一存储启动，完成后停止：

```sh
python3 -B scripts/project_docker.py start --storage-root .runtimes/prepared/build
.venv-experiment/bin/python -B tools/unified_smoke.py \
  --environment-profile .local/profiles/public-host.json \
  --evaluation-root .local/evaluation-v6-public60 --output-root '.audit/public synthetic'
python3 -B scripts/project_docker.py stop --storage-root .runtimes/prepared/build
```

Synthetic execution is forced local, additionally network-isolated, and uses
only shipped artificial tasks. It makes no model calls. Missing API keys still
fail the separate production check; key presence is never authentication proof.

合成入口强制本地执行器并额外禁网，仅使用人工样例，不调用模型。独立的生产
check 缺 key 仍失败，key 存在不表示服务鉴权通过。

## Historical verification / 历史复验

Both original P4 assets must match the frozen names, sizes and hashes in the
retained `release-assets.json`. The wrapper makes its optional external-checksum
contract mandatory, verifies the bounded whitelist before extraction, and
compares the newly scored JSON to the frozen per-task result. Expected output
is 45/60, 15 failed, 350 unscored. 44/60 remains the initial historical record;
333/410 and 81.22% are historical official results, not locally recomputed.

P4 原两件资产按保留 `release-assets.json` 的名称、大小、摘要核对。包装脚本将
原验证器可选的外部摘要改为必需，先验证有界白名单再解包，评分 JSON 与冻结逐题
结果一致才成功。预期 45/60、15 失败、350 未评分；44/60 为历史首轮记录，
333/410 和 81.22% 为历史官方成绩，不在本地重算。

The selection, six selected trace attempts and three missing-usage attempts
are preserved. Checksums establish integrity, not authorship signatures or
complete cost evidence. No reference answers, complete runtime, private mapping
or raw audit logs are published.

固定选择、六次精选轨迹和三次缺用量保留。摘要证明完整性，不证明作者签名或费用
完备。不发布参考答案、完整运行时、私有映射或原始审计日志。

## Reports, exit codes and cloud scope / 报告、退出码与云端范围

Actual preparation writes a fresh `.audit/preparation/<action>-<id>/` by default,
with `preparation-report.json` and private step logs. `--output` selects another
fresh directory under `.audit`. Exit 0 means preparation/static preflight
completed, 2 means a prerequisite or preparation step failed, and 130 means
interruption. Failed steps and daemon cleanup errors stay recorded.

默认实际准备写全新 `.audit/preparation/<action>-<id>/`，包含
`preparation-report.json` 和私有步骤日志；`--output` 可选其他全新 `.audit`
子目录。0 表示准备或静态检查完成，2 表示前提或准备步骤失败，130 表示中断。
失败步骤及 daemon 清理错误保留。

The new hosted workflow checks its actual candidate commit, using clean copies,
CPython 3.12.3/3.13.16, real Bubblewrap, locked installation, source/installed
tests, sdist-to-wheel builds and original public historical evidence. Full data
mode uses small artificial archives instead of repeating the full dataset
download. Only curated summaries are uploaded. None of these checks have been
run for this development candidate yet.

新托管工作流检验实际候选提交，在干净副本中使用 3.12.3/3.13.16、真实 Bubblewrap、
锁定安装、源码/安装后测试、sdist→wheel 和原公开历史附件。完整数据模式只用小型
人工归档，不重复全量下载；仅上传筛选摘要。当前候选的这些检查尚未执行。

The new provenance layer retains all prior JSON bytes, binds the post-release
README changes, and explicitly retires the removed default offline workflow.
No source or asset is reinterpreted as the original v0.1.0 candidate.

新来源层保留此前 JSON 原字节，绑定发布后 README 修改，并明确登记旧默认 offline
工作流的移除。不将新源码或新产物解释为原 v0.1.0 候选。
