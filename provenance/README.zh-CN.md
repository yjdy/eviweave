# 来源与提取边界

EviWeave — Evidence-Guided Data Agents，作者袁珺 / Jun Yuan（https://github.com/yjdy）。

本项目按历史实验 `relation_v6_full410_20261019T074114Z` 的 `frozen_src/` 提取首版 `relation-v6 + simple-fixes-v1 + Codex`，内部包名保留 `dataspace_baselines`。相邻 DataSpace 工作区只读，不使用其当前源码中的后续 Controlled 实现。源码来自 DataSpace；上游 MIT 声明保留在根目录 LICENSE，EviWeave 独立化整理与说明亦采用 MIT。数据通过原发布入口获取，不随代码打包。

`extraction-manifest.json` 是 22 个 Python 文件的提取白名单：来源 SHA-256、原样保留/允许适配政策，P1 完成后补充发布 SHA-256。原始审计、输入文件明细、尝试索引及预测索引仅保存在 `.audit/p0/`。`baseline-summary.json` 记录可公开的核验摘要，不含私有端点、凭据和原始日志。

允许适配仅限项目元数据、CLI、代理注册、配置路径与 doctor；提示词、导出器、预测验证/恢复、批次、适配器、运行器和隔离保持历史字节。通用模块内既有兼容性名称和沙箱 PATH 字符串保留，未提取对应的其他代理实现。

## 历史成绩的不同来源

- 本地首次公开 60 题评测：44/60，来源为历史 `before_failed_four_retry/results.json`。
- 固定四题恢复后的公开评测：45/60，来源为历史 `results.json` 及已有 `docs/opensource/v6-public60-eval.json`；P0–P1 仅核对已保存记录，不重新运行评测。
- 官方全量成绩：333/410、81.22%，按 2026-10-22 已有计划中的[官方榜单](https://dataspace-bench.github.io/#leaderboard)核验快照登记；本阶段不重新核验实时榜单。原登记名 `codex-controlled + Qwen3.8-Flash`、条目 ID `yjdy-codex-controlled-qwen38-flash` 指向这次历史 V6 提交，并不表示后续控制器实现进入本次发布范围。

22、23、207、273 固定采用 run_2，其余采用 run_1；不按预测内容或分数重选。三次最终 usage 缺失指 22、23、207 的 run_1 最终用量记录缺失，并非三个被采用的最终预测缺失；保持缺失，不补零。历史模型别名未记录不可变快照。

后续完整重评、精选轨迹、脱敏发布附件及线上发布留在 P4–P5；正式技术报告延后。
