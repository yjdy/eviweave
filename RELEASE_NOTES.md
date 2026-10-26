# EviWeave v0.1.1 — Release candidate / 发布候选

Status: pending review and publication. No publication date is assigned here. The online Release supplies the actual status and GitHub `published_at`; candidate source and packages are not rewritten to add that date.
状态：待审阅、待发布。本文未指定发布日期；实际状态及 GitHub `published_at` 以在线 Release 为准，不为补日期改写候选源码或安装包。

## English

This maintenance candidate adds the three source-distribution preparation entries (`prepare_environment.sh`, `prepare_dataspace.sh`, `verify_history.sh`), outer daemon shutdown/readiness fixes, current-source CI and the revised README. Package metadata, runtime version, both CLI aliases and citation use **0.1.1**. The wheel contains the business package and CLI; source-only tools, locks and provenance are in the sdist.

The V6 method, prompts, budgets, model policy, production isolation, recovery policy, configuration and dependencies are preserved. Among the 22 business files only the version declaration changes; the remaining 21 business files and all 17 verbatim protected files are unchanged. All preceding 19 provenance JSON records and the original v0.1.0 tag and assets are retained.

Support remains Linux x86_64, CPython **3.12.3 / 3.13.16** for full workflows and **3.10.22** for applicable package checks. Seven host-profile cases do not apply to 3.10; selected cases must have zero skips. Automatic lightweight CI does not establish full runtime acceptance. The candidate cloud-verification summary binds the actual tested commit, environments and results.

The historical official result is **333/410 (81.22%)**, evaluated on 2026-10-21 and checked on 2026-10-24. The initial public-set record is **44/60**; fixed historical recovery yields **45/60**. Offline verification reruns scoring only, using all **121** frozen materials and the original fixed predictions. It adds no benchmark inference or new score. Four historical recoveries, selected traces, three attempts with missing usage and the unknown immutable model snapshot remain documented in [experiments](EXPERIMENTS.md). G1 online collection remains unsupported; unknown costs remain unknown.

Candidate assets: wheel, sdist, `cloud-verification.json`, `release-manifest.json`, `SHA256SUMS`. The manifest binds package/verification identities; SHA256SUMS covers the other four files and excludes itself. Historical evidence is **linked**, not repackaged: [original v0.1.0 evidence ZIP](https://github.com/yjdy/eviweave/releases/download/v0.1.0/eviweave-v0.1.0-v6-evidence.zip) and [external SHA-256](https://github.com/yjdy/eviweave/releases/download/v0.1.0/eviweave-v0.1.0-v6-evidence.zip.sha256). v0.1.0 was published on **2026-10-24**; its bytes remain unchanged.

MIT code and documentation retain DataSpace attribution and **袁珺 / Jun Yuan** authorship. Upstream resource licenses and source boundaries remain in the provenance materials. EviWeave Controlled, G1, a formal report and new model experiments are outside this maintenance version. No credentials, reference answers, full traces, runtime images or private audit materials are release assets.

## 简体中文

本维护候选提供三个源码包准备入口（`prepare_environment.sh`、`prepare_dataspace.sh`、`verify_history.sh`）、外层 daemon 停止/就绪修复、当前源码 CI 和新版 README。包元数据、运行时版本、两个 CLI 与引用统一为 **0.1.1**。wheel 提供业务包和 CLI；工具、锁文件及来源材料由 sdist 提供。

V6 方法、提示词、预算、模型政策、生产隔离、恢复政策、配置及依赖保持不变。22 个业务文件仅版本声明变化，其余 21 个业务文件及全部 17 个原样保护文件不变；此前 19 份来源 JSON、原 v0.1.0 标签及附件均保留。

支持范围仍为 Linux x86_64：CPython **3.12.3 / 3.13.16** 支持完整流程，**3.10.22** 为包级适用检查。七项宿主档案用例不适用于 3.10；所选用例必须零跳过。自动轻量检查不代表完整运行验收通过，候选 cloud-verification 摘要绑定实际被测提交、环境及结果。

历史官方成绩为 **333/410（81.22%）**，评测日期 2026-10-21，核验日期 2026-10-24；公开集首轮为 **44/60**，固定历史恢复后为 **45/60**。离线复验仅用全部 **121** 项冻结材料和原固定预测重新评分，不新增 benchmark 推理或成绩。四题历史恢复、精选轨迹、三次用量缺失、不可变模型快照未知等边界见[实验说明](EXPERIMENTS.zh-CN.md)。G1 在线采集仍不支持，未知费用继续记为未知。

候选资产为 wheel、sdist、`cloud-verification.json`、`release-manifest.json`、`SHA256SUMS`。清单绑定软件包及验收摘要身份，SHA256SUMS 覆盖其余四项并排除自身。历史证据只链接[原 v0.1.0 ZIP](https://github.com/yjdy/eviweave/releases/download/v0.1.0/eviweave-v0.1.0-v6-evidence.zip)和[外部 SHA-256](https://github.com/yjdy/eviweave/releases/download/v0.1.0/eviweave-v0.1.0-v6-evidence.zip.sha256)，不重新打包。v0.1.0 已于 **2026-10-24** 发布，其原字节保持不变。

代码与说明采用 MIT，保留 DataSpace 归属及**袁珺 / Jun Yuan**署名；上游资源许可证及来源边界见来源材料。Controlled、G1、正式报告及新模型实验不在本维护版本范围；凭据、参考答案、完整 trace、运行时镜像及私有审计材料不作为发布资产。
