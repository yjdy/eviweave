# Current main CI / 当前 main 检查

Author / 作者：袁珺 / Jun Yuan. Code and documentation: MIT; retain DataSpace attribution.

The preparation workflow now distinguishes automatic package checks from manually requested full acceptance. Main pushes and pull requests targeting main check the event commit, including the GitHub merge-test commit for pull requests. Reports bind workflow and tested commits, tree and file fingerprint; historical release verification remains fixed to v0.1.0.

准备工作流区分自动包级检查与手动完整验收。main 推送及面向 main 的 PR 检验事件提交；PR 使用 GitHub 合并测试提交。报告登记工作流与被测提交、tree和逐文件指纹；历史发布核验仍绑定 v0.1.0。

Automatic checks cover CPython 3.10.22/3.12.3/3.13.16, ordinary installation, source tests, sdist-to-wheel, installed tests, package/source identity and bilingual documentation. Only the existing seven host-profile cases are explicitly not applicable on 3.10; they remain mandatory on 3.12/3.13. Discovery is dynamic; failures never extend the exclusion list. All selected cases must pass without skips.

自动检查覆盖 CPython3.10.22/3.12.3/3.13.16、普通安装、源码测试、sdist→wheel、安装后测试、来源与包内容及双语说明。3.10仅明确排除已有七项宿主档案专用用例，3.12/3.13仍全部必测。动态发现测试，不因失败扩大排除；所选测试必须通过且零跳过。

Manual `full=true` first requires the package matrix, then runs both existing runtime languages serially. The default `full=false` reports full validation as NOT_REQUESTED. A package pass alone is not a full runtime pass. Ordinary pushes do not cancel a manual full run. Workloads use hosted runners, read-only repository access and checkout-local caches, HOME and output; only curated summaries leave the runner. No real model, full dataset download, online cost collection or publication occurs.

手动 `full=true` 先通过包级矩阵，再串行执行已有双语运行时验收。默认 `full=false`，报告完整验收为 NOT_REQUESTED；包级通过不冒充完整通过。普通推送不取消手动完整运行。工作负载仅使用托管runner、只读仓库权限和checkout内缓存、HOME及输出；仅传出精简摘要。不调用模型、不下载完整数据、不采集在线费用或发布。

`main-ci-baseline.json` extends the accepted 16ef243 source. It retains all 18 preceding provenance JSON files byte-for-byte and records only this follow-up's predecessors and current bytes. Its own hash is external. The original release's README digests and assets remain historical identities, not stale fields to overwrite. Build products are CI evidence, not replacement release assets.

`main-ci-baseline.json` 延续已验收16ef243源码，原字节保留此前18份来源JSON，仅登记此次修改的前驱和当前摘要，自身摘要由外部绑定。原发行README摘要与资产属于历史身份，不追改；验收构建物不替换已发布资产。
