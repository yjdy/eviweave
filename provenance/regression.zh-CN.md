# P3 离线行为回归与证据边界

作者：袁珺 / Jun Yuan；代码与说明采用 MIT，保留 DataSpace 上游声明。

业务代码的唯一来源继续是全量实验的 frozen_src。P3 不修改 22 个已发布 Python 模块，其中 17 个保护文件保持冻结字节；没有新增生产 harness 或运行依赖。测试使用人工构造的 CSV、JSON、哨兵和任务，不包含真实 benchmark、历史预测或完整 trace。

历史测试单独来自 DataSpace Git 提交 `59ed446d74dd496572d027878925ff04d1315db2`，逐文件来源摘要、发布摘要及调整理由见 [测试来源清单](regression-baseline.json)。运行器和批次测试按原文件提取，调整临时目录、格式及取消同步；适配器只提取 Codex 相关方法，隔离测试按历史挂载场景拆为结构检查与真实历史 rootfs 验收，不导入其他代理。

统一入口为 `tools/run_regression.py`：

| 层次 | 必需材料 | 检查范围 |
|---|---|---|
| unit | 安装包及源码发行中的测试/工具 | 导出保真/空表/错误及原子写入；CSV 合同；嵌套恢复与拒绝、复制变化/发布冲突；运行状态、超时/取消/清理；Codex 配置/trace；并发、最终状态及续跑身份 |
| reference | 显式提供的冻结包来源 | 两个独立子进程执行同一组合成案例，各自断言人工预期，并比较字节、摘要、稳定错误、状态、事件和尝试；原日志保留 |
| runtime | 经 P2 清单验证的历史 rootfs 和 Codex 0.145.0 | 原始外层的只读/可写、跨任务/链接/进程路径、PID 与网络命名空间；单题、并发 1/2、归集和续跑；配置生命周期及附加禁网合成诊断 |

输出必须是项目内全新或空目录，拒绝符号链接路径。缺少强制参数/环境返回 2，行为失败返回 1；缺少沙箱或运行时不能通过 unit 代替。测试子进程清除真实凭据和用户 Python 路径，仅使用无效测试值；超时及取消有有限截止时间。

## 沙箱与 trusted 差异

原始生产外层保持网络命名空间，PID/IPC/UTS 隔离，工作台、输入、Codex 和导出器只读，任务输出/HOME 可写。真实外层探测不添加 `--unshare-net`，仅观察网络命名空间，不发起外部连接。

内层配置由冻结适配器生成：`workspace-write`、`network_access=false`、`writable_roots=/mnt/output`、`approval_policy=never`、任务独立 HOME 和凭据过滤。历史 task_1 的运行后配置额外包含 `projects."/mnt/output".trust_level="trusted"`。只读历史对照保留该差异，不复制历史 HOME，也不修改生产模板。

固定 CLI 的本地 `--strict-config app-server --listen stdio://` 可执行 initialize/config/read，无需推理请求。诊断在附加禁网环境分别读取新模板及仅增加 trusted 段的私有副本；有效配置只相差 projects，显式沙箱、网络、审批、预算、环境过滤一致，诊断前后配置摘要不变。它证明本地配置加载和服务初始化，并不证明真实 exec、模型工具内层执行或服务认证。历史 trusted 的写入者/时机仍未证实；这部分保持未执行/未证明。

另探测的 `codex sandbox` 要求新的权限 profile 且不支持生产 `--strict-config`，不能作为冻结配置的执行证据。没有为此添加新 profile、移除生产参数或改动沙箱。

外层宿主 bwrap 版本、二进制摘要、权限及内核在私有报告单独登记；工作台内部 bwrap 版本独立记录。历史未冻结宿主 binary 摘要，不能声称历史宿主逐字节认证。检查只覆盖列明边界，不代表完整安全审计。

## CI 与未执行事项

轻量 CI 配置 Python 3.10/3.12/3.13，只构建、安装并运行 unit；不需要 DataSpace 或下载真实运行时。`tools/ci_check.py` 用同一命令检查普通安装、源码包构建 wheel、独立安装、两入口、依赖与保护摘要，以及源码包自带的测试。

Actions 固定完整提交：checkout v4.2.2 `11bd71901bbe5b1630ceea73d27597364c9af683`；setup-python v5.6.0 `a26af69be951a213d495a4c3e4e4022e16d87065`。2026-10-22 通过官方 Git 仓库 tag refs 核对。工作流仅 contents:read，支持 push/pull_request/workflow_dispatch。

本地实际解释器与托管状态分别记录；配置 3.10 不表示本地已运行 3.10。托管 CI 留 P5 首次推送时执行。本阶段不运行真实 codex exec、模型调用、benchmark 重跑或新成绩评测，不启动 P4–P5、公开推送或正式技术报告。私有 docs、审计、参考源码和真实运行时不进入 Git 或发行包。
