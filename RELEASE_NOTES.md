# v0.1.0 — Pending release / 待发布

Release date: **not assigned**. Final commit, final wheel/sdist and aggregate checksum binding: **pending stage four**. This file is prepared Release body text, not evidence of a published release. The intended repository is `https://github.com/yjdy/eviweave`; anonymous API access returned 404 on 2026-10-24, which does not distinguish absent from nonpublic. No repository was created or modified during preparation.

发布日期：**未指定**。最终提交、最终 wheel/sdist 和汇总校验和绑定：**留待第四阶段**。本文是准备好的 Release 正文，不表示已发布。拟发布仓库为 `https://github.com/yjdy/eviweave`；2026-10-24 匿名 API 返回 404，不能区分不存在或非公开；整理期间未创建或修改仓库。

## English Release body

EviWeave 0.1.0 prepares the frozen historical **relation-v6 + simple-fixes-v1** baseline with **Codex 0.145.0**, **Qwen3.8-Flash** and **Responses**. It supports explicit public resource preparation, independent host profiles, locked installation, single/batch/repeat/resume experiments, offline verification and offline cost analysis. **This release is pending publication.**

The full workflow targets Linux x86_64 and explicitly checked CPython 3.12/3.13 hosts. Local acceptance used 3.12.3 and 3.13.16 on one physical host; Python >=3.10 is package metadata, not an accepted full-runtime matrix. Hosted CI, Python 3.10 execution and another physical host remain unverified. An independent public workbench rebuild has a different full-tree identity from the historical import. The wheel provides the 22 business modules and CLI aliases; the sdist/source also provides preparation and experiment tools.

Historical official result: **333/410, 81.22%**, evaluated 2026-10-21, verified 2026-10-22; the official page still listed a tie for first on the 2026-10-24 recheck. Historical public first pass **44/60** and fixed-recovery offline **45/60** have different scopes. Later independent **43/60** and task-33 **1/3 attempts passed, current selection 0/1** do not replace those historical scores. See [experiment notes](EXPERIMENTS.md) for recovery, usage and model-snapshot limits. No new inference was performed to prepare this release text.

Candidate assets are a Python wheel, source archive, the original `eviweave-v0.1.0-v6-evidence.zip`, its original `.sha256`, and a later aggregate `SHA256SUMS`. The evidence ZIP is **1,980,385 bytes**, SHA-256 **`3e40cd0e0154f9c81f643dffc5eced748e9bed2ab376200ff49b921ee4c27b39`**. It contains 410 unchanged predictions, 414 attempt summaries, a fixed selection table and selected redacted traces for 5 tasks / 6 attempts, not all original traces. Three missing usage records remain unknown. Full traces, private runtime images, reference answers, newer real-run logs and private redaction mappings are excluded. See the [candidate asset manifest](provenance/release-assets.json); final software hashes and commit are intentionally unbound.

Real `run` calls a model, defaults to **public60**, and incurs charges. Only the user's dedicated `EVIWEAVE_API_KEY` is accepted. Confirmed final authentication failure stops dispatch after the current task returns and cancels in-flight work; normally submitted wrong answers are not retried by resume. Offline imports cannot establish complete HTTP enumeration, pre-request persistence or total paid cost. Complete online collection remains **UNSUPPORTED_G1**; executable unified v2 and later EviWeave Controlled are not included. Old analysis v1 remains read-only; revaluation uses a new output and `decimal-quantum-v2` per-request rounding.

Code/documentation: MIT, retaining DataSpace Authors' notice and **袁珺 / Jun Yuan** authorship. Datasets, predictions and third-party runtimes retain their original terms. Use [README](README.md) and [CITATION.cff](CITATION.cff). Final clean-checkout acceptance, commit/build binding and authorized remote publication must finish before this can be presented as released.

## 中文 Release 正文

EviWeave 0.1.0 准备交付冻结的历史 **relation-v6 + simple-fixes-v1**，使用 **Codex 0.145.0**、**Qwen3.8-Flash** 和 **Responses**，提供显式公开资源准备、独立宿主档案、锁定安装、单题/批次/重复/续跑、离线验证和离线费用分析。**本版仍待发布。**

完整流程面向 Linux x86_64 和显式检查的 CPython 3.12/3.13 宿主。本地验收使用同一物理宿主的 3.12.3 和 3.13.16；Python >=3.10 是包元数据，不是完整运行时验收矩阵。托管 CI、Python 3.10 实测和其他物理宿主仍未验证。公开独立工作台重建与历史导入的全树身份不同。Wheel 提供 22 个业务模块及 CLI 别名，sdist/源码另提供准备和实验工具。

历史官方结果为 **333/410、81.22%**，评测 2026-10-21，核验 2026-10-22；2026-10-24 复核时官方页面仍列为并列第一。历史公开集首轮 **44/60** 与固定恢复离线 **45/60** 口径不同。后续独立 **43/60** 和第33题**单次通过数 1/3、当前选择 0/1** 不替换历史成绩。恢复、用量和模型快照边界见[实验说明](EXPERIMENTS.zh-CN.md)。准备本文没有新增推理。

候选附件为 Python wheel、源码包、原 `eviweave-v0.1.0-v6-evidence.zip`、原 `.sha256` 和后续汇总 `SHA256SUMS`。证据 ZIP 为 **1,980,385 字节**，SHA-256 **`3e40cd0e0154f9c81f643dffc5eced748e9bed2ab376200ff49b921ee4c27b39`**，含 410 份原字节预测、414 条尝试摘要、固定选择表和 5 题 / 6 次尝试的精选脱敏轨迹，不是完整原始轨迹集。三次缺失用量保留未知。完整轨迹、私有运行时镜像、参考答案、新真实运行日志和私有脱敏映射均排除。见[候选资产清单](provenance/release-assets.json)，最终软件摘要和提交有意留空待绑定。

真实 `run` 调用模型，默认 **public60**，会产生费用；只接受用户专用 `EVIWEAVE_API_KEY`。当前任务返回后确认最终鉴权失败即停止派发并取消在途；正常提交但答案错误的题不由 resume 重试。离线导入不能证明完整 HTTP 枚举、请求前持久化或实际支付总额。完整在线采集仍为 **UNSUPPORTED_G1**，不含可执行统一 v2 或后续 EviWeave Controlled。旧分析 v1 只读，重估使用全新输出和 `decimal-quantum-v2` 逐请求舍入。

代码/文档采用 MIT，保留 DataSpace Authors 声明及 **袁珺 / Jun Yuan** 署名；数据、预测和第三方运行时保留原条款。使用见 [README](README.zh-CN.md) 与 [CITATION.cff](CITATION.cff)。完整干净检出验收、提交/构建绑定及获授权的远程发布完成前，不得标记首版已发布。
