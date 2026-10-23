# P5.3 documentation and pending publication / 文档与待发布材料

Prepared **2026-10-24 (Asia/Shanghai)**, with **no model calls, benchmark reruns, online collection or remote writes**. This stage prepares documentation; final clean-checkout acceptance, final candidate binding and publication remain pending. Code/documentation: MIT, retaining DataSpace attribution and **袁珺 / Jun Yuan** authorship.

本轮于 **2026-10-24（Asia/Shanghai）**整理文档，**没有模型调用、benchmark 复跑、在线采集或远程写入**。完整干净检出验收、最终候选绑定和发布仍待完成。代码/文档采用 MIT，保留 DataSpace 归属及**袁珺 / Jun Yuan** 署名。

## Implementation basis / 实现依据

The starting source is `655938d8683c07541ecdf11852d0c2b89c3a0545` on `codex/p5-release`; all 145 tracked files match the accepted P5.2 candidate byte for byte. The earlier P5.2 report retains its implementation-time predecessor `5ebe9e17544c24090db44cc828cac0352bd4860d`, not the current HEAD. Nothing was reset or overwritten.

起点为 `codex/p5-release` 的 `655938d8683c07541ecdf11852d0c2b89c3a0545`，145 个跟踪文件与 P5.2 已验收候选逐字节相同。旧 P5.2 结果保留实施时前驱 `5ebe9e17544c24090db44cc828cac0352bd4860d`，不冒充当前 HEAD；未重置或覆盖已有材料。

Usage follows actual `--help`, code and accepted command logs, rather than planned interfaces:

| Reader task / 使用任务 | Actual entry and contract / 实际入口与合同 |
|---|---|
| Resources / 资源 | [public_resources.py](../tools/public_resources.py): codex, artifacts, workbench, evaluation, dataset, verify-workbench; [fixed resource lock](public-resources-lock.json) |
| Candidate host / 候选宿主 | [host_profile.py](../tools/host_profile.py): collect → check → explicit selection; config and offline doctor |
| Installation / 安装 | [install_environment.py](../tools/install_environment.py), [env.sh](../scripts/env.sh), [locks](../locks/README.md); authenticated wheels and target-interpreter config generation |
| Unified workflow / 统一流程 | [experiment.py](../tools/experiment.py), [unified contract](unified.zh-CN.md); snapshot profile and identity-bound resume |
| Offline scoring / 离线评分 | [standalone.py](../tools/standalone.py), [121-file lock](evaluation-lock.json) |
| Evidence / 附件 | [package_evidence.py](../tools/package_evidence.py), [P4 evidence contract](evidence.zh-CN.md), [baseline summary](baseline-summary.json) |
| Offline costs / 离线费用 | [observability use](observability.zh-CN.md), [failure/import fix](bugfix.zh-CN.md), [config/rounding fix](config-rounding.zh-CN.md) |

P5.2 accepted CPython 3.12.3 and 3.13.16 on the same physical host: each had 228 source tests and 228 installed tests, locked installation and packaging checks. Its actual Bubblewrap synthetic execution tested interruption/recovery and zero new attempts on completed resume, with artificial score 2/2. These historical results are reused, not claimed as rerun in P5.3; this stage separately checks documents, metadata, packaging and provenance.

P5.2 在同一物理宿主的 CPython 3.12.3 和 3.13.16 上，每版源码 228 项、安装后 228 项通过，并验收锁定安装及打包。真实 Bubblewrap 合成执行覆盖中断/恢复及完成后零新增，人工评分 2/2。本轮复用这些历史证据，不声称重新执行；本轮另行检查文档、元数据、打包与来源。

The independent rebuild has a distinct identity: all 329 debs and 21 workbench wheels were actually downloaded and their installed inventories matched; its 19,584-entry inventory differs from the historical 18,516-entry inventory in 3,620 entries. This is not acceptance of a second physical host. Old `libexpat1` retains the official-HTTPS observed-hash trust boundary; its historical signed index was unavailable. Exact original profiles, dependency locks, production isolation, prompts, budgets and recovery policy are unchanged.

独立重建身份分开登记：329 个 deb、21 个工作台 wheel 全部实际下载并匹配安装清单；新清单 19,584 项，与历史 18,516 项清单有 3,620 项差异，不证明第二台物理宿主通过。旧 `libexpat1` 保留官方 HTTPS 观察摘要信任边界，历史签名索引未取得。原精确档案、依赖锁、生产隔离、提示词、预算和恢复政策不改。

P5.2 downloaded the complete 2,878,651,222-byte dataset ZIP and matched its official SHA-256; the user then deleted it. Closeout verified the remaining 410-task tree: 7,978 files, 15,010,873,064 bytes, matching ZIP-index sizes/CRC32. The included `checksums.sha256` covered 7,977 files, all matching; the checksum list itself had size/CRC coverage, not an independently frozen external SHA. P5.3 does not redownload the ZIP or claim a fresh full-data hash check.

P5.2 已下载完整 2,878,651,222 字节 ZIP 并匹配官方 SHA-256，随后用户删除 ZIP。收尾核验现存 410 题、7,978 文件、15,010,873,064 字节，匹配 ZIP 索引大小/CRC32；内含 `checksums.sha256` 覆盖 7,977 文件且全部匹配，但该清单自身只有大小/CRC 覆盖，无独立外部冻结 SHA。本轮不恢复 ZIP，不声称新增完整数据摘要核验。

## Read-only sources / 只读来源

Anonymous public requests were saved with UTC retrieval times, HTTP status, headers and SHA-256 in the private stage-three audit. Dates below use Asia/Shanghai; full snapshots remain private.

匿名公共请求的 UTC 获取时间、HTTP 状态、响应头和 SHA-256 保存在第三阶段私有审计；下表日期采用 Asia/Shanghai，完整快照不公开。

| Source / 来源 | 2026-10-24 observation / 观察 |
|---|---|
| [Official leaderboard](https://dataspace-bench.github.io/#leaderboard) and [served script](https://dataspace-bench.github.io/assets/index-CJ-jdqTo.js) | HTTP 200; 333/410, 81.22%, evaluated 2026-10-21. Accuracy equals the maximum of eight served entries, two tied; supports a dated tied-first observation, not future rank. / HTTP 200，条目与历史相同，八个条目最高准确率有两个并列，仅支持本次日期的观察。 |
| [Pinned upstream README](https://github.com/HKUSTDial/DataSpace/blob/6491caa4c70cc06cacb6103ba73cefb00746abfe/README.md) | Raw content HTTP 200; fixed official source/data entry retained. / 原始内容 HTTP 200，保留固定官方来源/数据入口。 |
| [Pinned dataset release](https://huggingface.co/datasets/HKUSTDial/DataSpace/tree/96be19257d7aa251dbce4771d46a26fc36f6dc52/release) | API HTTP 200; [official checksum](https://huggingface.co/datasets/HKUSTDial/DataSpace/resolve/96be19257d7aa251dbce4771d46a26fc36f6dc52/release/DataSpace-Benchmark.zip.sha256) HTTP 200, unchanged declaration; no full download here. / API 与官方摘要 HTTP 200，整包声明不变，本轮未下载整包。 |
| Intended `https://github.com/yjdy/eviweave` | Anonymous API 404 cannot distinguish absent from nonpublic; CITATION URL is an intended destination, not a verified public release. / 匿名 API 404，不能区分不存在或非公开；CITATION URL 是拟发布位置，不证明已公开。 |

Historical verification remains dated 2026-10-22 and official evaluation 2026-10-21; the P4 ZIP was not regenerated. Later conclusions in EXPERIMENTS were checked against saved score/summary and experiment records: public60 43/60, independent task-215 pass, two-task 2/2, and task-33 1/3 attempts with latest selection 0/1. The logs/predictions remain private; no best-attempt substitution or new experiment occurred.

历史核验日期仍为 2026-10-22，官方评测为 2026-10-21，不重建 P4 ZIP。EXPERIMENTS 的后续结论已对照现存 score/summary 与实验记录：public60 43/60、第215题独立通过、两题 2/2、第33题三次 1/3 且最新选择 0/1。日志/预测仍私有，不按最好尝试替换，不增加实验。

## Assets, attribution and next gates / 资产、归属与后续门槛

[release-assets.json](release-assets.json) is a pending allowlist, not an upload receipt. Only the original P4 ZIP and external checksum bind observed bytes. Software archive sizes/hashes, final commit, aggregate SHA256SUMS and release date remain unassigned. [RELEASE_NOTES.md](../RELEASE_NOTES.md) supplies bilingual draft bodies. Complete original traces, private runtimes, gold, original datasets, newer real-run logs and private redaction mappings are excluded.

[release-assets.json](release-assets.json) 是待发布白名单，不是上传回执。仅原 P4 ZIP 与外部摘要绑定已观察字节；软件包大小/摘要、最终提交、汇总 SHA256SUMS 和发布日期留空。[RELEASE_NOTES.md](../RELEASE_NOTES.md) 提供双语正文。完整原始轨迹、私有运行时、gold、原数据、新真实运行日志和私有脱敏映射排除。

MIT covers project code/documentation with the DataSpace Authors notice and EviWeave attribution. It does not relicense datasets, historical predictions, complete runtime images or third-party packages. Original P4 terms remain. No DOI/date is invented; CITATION identifies Jun Yuan / 袁珺 and 0.1.0 with an explicit pending message.

MIT 覆盖项目代码/文档，保留 DataSpace Authors 及 EviWeave 声明，不为数据、历史预测、完整镜像或第三方包重新授权；保留 P4 原条款。不虚构 DOI/日期；CITATION 登记 Jun Yuan / 袁珺、0.1.0 和明确待发布说明。

Complete online collection remains UNSUPPORTED_G1; executable unified v2, Controlled and formal reports are future work. Real service authentication/account billing, another physical host, Python 3.10 execution, hosted CI, two isolated clean checkouts and publication are not passed claims. No P5.2 product defect required repair. The only executable change extends release checking to authenticate retained P5 history and require new documents in the sdist; business execution and experiment identity inputs are unchanged.

完整在线采集仍为 UNSUPPORTED_G1；可执行统一 v2、Controlled 和正式报告属后续工作。真实服务鉴权/账户账单、其他宿主、Python 3.10 实测、托管 CI、两份隔离干净检出和发布均不冒称通过。未发现需修复的 P5.2 产品缺陷；唯一可执行变更扩展发行检查，认证旧 P5 来源层并要求新文档进入 sdist，不改业务执行或实验身份输入。

The [documentation provenance layer](p5-documentation-baseline.json) records each predecessor/release SHA-256 and reason, retains all previous provenance JSON bytes, and omits its own hash to avoid circularity. Private plans/handoff/audit stay local and excluded. Full clean-checkout acceptance and final candidate freezing remain stage four; remote publication remains stage five.

[文档来源层](p5-documentation-baseline.json)逐文件登记前驱/交付 SHA-256 和理由，保留此前全部来源 JSON，不自含摘要以免循环。本地方案/交接/审计不进入 Git 或发行包。完整干净检出与最终候选冻结留第四阶段，远程发布留第五阶段。
