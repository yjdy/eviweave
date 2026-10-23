# EviWeave v0.1.0: experiment notes

[简体中文](EXPERIMENTS.zh-CN.md) · [Usage](README.md)

This is a short account of existing evidence, not a new benchmark run or a formal technical report. The first version freezes relation-v6 + simple-fixes-v1, Codex 0.145.0, Qwen3.8-Flash and Responses. The 22 business modules, including 17 verbatim protected files, preserve the historical source boundary. Later **EviWeave Controlled is outside this release**, even though the historical official entry was registered as `codex-controlled + Qwen3.8-Flash`.

## Historical results

| Evidence | Result | Interpretation |
|---|---|---|
| Official full benchmark | 333/410, 81.22% | Evaluated 2026-10-21; historical verification 2026-10-22 recorded tied first |
| Historical public subset, first pass | 44/60 | Preserved first-pass record, not recomputed inference |
| Historical public subset, fixed recovery selection | 45/60 | Offline evaluation of existing selected predictions; 15 failed, 350 full-benchmark tasks unscored locally |

The [official leaderboard](https://dataspace-bench.github.io/#leaderboard) was rechecked on **2026-10-24 (Asia/Shanghai)**: its served JavaScript still listed 333/410, 81.22%, evaluated 2026-10-21 and tied first. This dated observation does not guarantee a live ranking. The original P4 snapshot and attachment are unchanged. Local public scoring does not independently recompute the official 410-task result. Source revisions and recheck details are recorded in [publication provenance](provenance/p5-documentation.md).

There are 414 historical attempts: 410 submitted, 3 runtime_error and 1 no_prediction. Tasks **22, 23, 207, 273** each received one operator-triggered recovery after missing output; their fixed selection is run_2, with all other tasks using run_1. This is the preserved historical policy, not automatic retry-until-correct or selection by score. The current unified resume skips normally submitted wrong answers and selects the latest allocated attempt, even if it fails.

The frozen evaluator and 60 configurations/60 reference answers are checked against the original **121-file lock**. The official evaluator source is pinned at GitHub `6491caa4c70cc06cacb6103ba73cefb00746abfe`; scoring members at Hugging Face `96be19257d7aa251dbce4771d46a26fc36f6dc52`. Historical offline acceptance used CPython 3.12.3. Preparing resources does not run inference; reference answers are never mounted into the solver workspace.

## Later independent runs

These existing local observations were read, not rerun, during document preparation. They are separate experiments and do not revise historical scores. Their raw logs and predictions are not added to the public evidence ZIP.

| Date (Asia/Shanghai) / existing record | Saved result |
|---|---|
| 2026-10-22, three-task pilot (33, 35, 23) | 2/3; actual model/client/sandbox/export/scoring chain observed; CPython 3.12.3 |
| 2026-10-22, additional task 33 | Failed; prediction bytes matched the preceding pilot failure; no further attempt added |
| 2026-10-23, independent public60 | 43/60, including task 215 missing prediction; interpreter version not independently recorded in these result files |
| 2026-10-23, independent task 215 recovery | One submitted attempt passed; CPython 3.13.16; does not change the preceding 43/60 |
| 2026-10-23, tasks 23 and 35 | 2/2, each first attempt; CPython 3.12.3 |
| 2026-10-23, three task-33 trials | 1/3 attempts passed; run_1 passed, run_2/run_3 failed; fixed latest run_3 gives current task accuracy 0/1; CPython 3.12.3 |

These runs used the frozen V6 policy, the model alias and the historical imported runtime on the relocated local host. They do not validate model performance in the later public rebuild. The 43/60 comparison with historical 45/60 records 40 common passes, 3 improvements, 5 regressions and 12 common failures. Neither later task-215 success nor the best task-33 trial is substituted into an earlier score. Small samples do not establish a stable success probability or a cause for variation.

## Evidence and limits

The pending P4 asset `eviweave-v0.1.0-v6-evidence.zip` has 445 files: 410 unchanged predictions, 414 attempt summaries, the fixed selection table and **selected redacted traces** for tasks 33, 328, 35, 391 and 23 (six attempts, including task 23's failure/recovery). It is not the complete original trace collection. See [asset list](provenance/release-assets.json) and [evidence contract](provenance/evidence.zh-CN.md). Original full traces, private runtimes, gold answers, newer real-run logs and private redaction mappings remain excluded. Source-log hashes were added during P4; they are not proof of contemporaneous trace freezing. Checksums are not digital signatures.

Usage is missing for **22/run_1, 23/run_1, 207/run_1**. Missing values remain null, not zero. Totals cover recorded observations only; cached/reasoning subfields are not added to parent token fields. Complete HTTP enumeration and actual account cost were not certified. A displayed upstream cost is not independently validated here; offline cost import cannot repair that evidence gap or establish pre-request persistence. Full online collection remains **UNSUPPORTED_G1**.

`qwen3.8-flash` is a requested model alias. No immutable serving snapshot was saved; server routing/sampling identity is unknown. Matching prompts, budgets and runtime files cannot guarantee identical new predictions or 333/410. Environment identity, historical evidence integrity, service authentication and answer accuracy are separate claims. The independent public rebuild matches the locked package/tool lists but differs from historical full-tree contents. P5.2's synthetic 2/2 and its Python 3.12.3/3.13.16 offline tests are infrastructure checks, not new benchmark results; a second physical host, Python 3.10 execution and hosted CI remain unverified.

No new model calls, benchmark reruns, online collection or experiments were performed for these notes. Code/documentation use MIT with DataSpace attribution and 袁珺 / Jun Yuan authorship; data, predictions and runtime components retain their original source terms.
