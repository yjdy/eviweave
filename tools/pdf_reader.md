# PDF 读取器

`pdf_reader.py` 是一个本地命令行工具。普通文本提取需要 Python 3.10+ 和 Poppler 的 `pdfinfo`、`pdftotext`。判断空页和运行 OCR 还需要 `pdftoppm`。表格模式使用 `pdfplumber`，OCR 使用本地 `tesseract` 及指定语言数据。EviWeave 锁定的工作台已有这些依赖。

```sh
python3 tools/pdf_reader.py search input.pdf '档案171'
python3 tools/pdf_reader.py parse input.pdf --out document.json
python3 tools/pdf_reader.py parse input.pdf --pages 4,9-10 --coordinates
python3 tools/pdf_reader.py read document.json --pages 4,9-10
python3 tools/pdf_reader.py find document.json '档案171'
```

页码是从 1 开始的物理页码。JSON 绑定原 PDF 的 SHA-256，保留原生分页文本和警告。搜索匹配时忽略空白，返回命中页码、原文位置及片段。坐标以可见页左上角为原点，单位为 PDF 点。

## V8 单题入口

`tools/run_v8.py` 使用现有 Codex 配置和任务目录，启动一个 V8 单题任务。任务有 PDF 时，工作台会只读挂载读取器。V8 提示词要求统一用它提取 PDF，按需查找、选页和回查原文。读取器在工作台的路径是 `/mnt/runtime/eviweave-pdf-reader.py`。

```sh
python3 -B tools/run_v8.py --config CONFIG.yaml --task-dir TASK_DIR --run-dir RUN_DIR
```

## 统一实验入口

新实验用 `--version v8` 选择 V8，省略版本时默认 V6。安装、配置、数据和评分资源沿用统一实验入口的准备流程。

```sh
./scripts/experiment.sh check --version v8 --tasks 33
./scripts/experiment.sh run --version v8 --scope public60 --output .audit/public60-v8
./scripts/experiment.sh run --resume --output .audit/public60-v8 --yes
./scripts/experiment.sh verify --version v8 --experiment .audit/public60-v8
```

`check` 检查本地资源，不调用模型。`run` 会调用模型。`run --resume` 从实验记录恢复版本并补跑未完成的尝试。`verify` 用实验内的评分快照离线验收，`summary.json` 同时列出各次尝试的语义核对状态。续跑和验收都可用 `--version v8` 核对实验版本。此前用本地批次脚本创建的目录继续沿用对应脚本。

### 导出前语义核对

所有 V8 任务在同一次会话中核对取数表与记录粒度、统计范围、字段、排序和输出列。模型写好候选行后，将题干和原件支持的要求、实际选择和证据位置记入 `output/semantic-review.json`，再调用工作台内的本地核对脚本。脚本比较所记录的选择，供当前会话在导出前修正有证据的冲突。无法定论的选择记为 `uncertain`。

运行器处理完 CSV 后，在运行目录写入 `semantic-review-result.json`，包括五项状态和最终 CSV 表头的逐列对照。报告用于诊断，CSV 仍按现有格式检查和提交规则处理。语义核对依赖会话记录的证据，脚本不会自行判定业务含义。

### PDF 原件回查

`search` 返回命中页、原文片段、源 PDF 摘要以及以字符计的 `start`、`end`。默认最多返回 20 项，`truncated` 表示还有命中，`page_issues` 列出读取失败或待复核的页。用 `parse --pages` 读取所需页面，再把选中的命中字段交给 `verify`。它从原 PDF 重新读取指定页，比较同一位置的确切原文。OCR 页使用相同的语言参数，结果会标明来源是 OCR。

```sh
python3 /mnt/runtime/eviweave-pdf-reader.py verify "$PDF" --sha256 "$SHA" \
  --page "$PAGE" --start "$START" --end "$END" --quote="$QUOTE"
```

带中文编码的原生 PDF 需要 Poppler 映射数据。若锁定工作台缺少该数据，把本机已安装的 `poppler-data` 导入项目内的 `.local/v8-poppler-data`。V8 只读挂载这份本地副本。

```sh
mkdir -p .local/v8-poppler-data
cp -a /usr/share/poppler/. .local/v8-poppler-data/
cp /usr/share/doc/poppler-data/copyright .local/v8-poppler-data/COPYRIGHT
```

## 定向提表

把已经在原页定位的区域写入 JSON，再运行 `parse input.pdf --table-spec regions.json`。每项指定物理页码和矩形。可同时给定列、行边界，数组包括最外侧两条边界。

```json
[
  {
    "page": 9,
    "bbox": [78, 250, 516, 454],
    "columns": [78, 180, 292, 516],
    "rows": [250, 284, 318, 352, 386, 420, 454]
  }
]
```

有行列边界时，完整网格的状态为 `extracted`。只给矩形时，输出按文字位置提取的候选，状态为 `needs_review`。表格派生结果保存在对应页，正文仍可单独读取。

## OCR 与错误状态

原生文字为空或出现替换字符时，先检查页面渲染。空白和孤立分隔线页标记为 `blank`。有内容的可疑页走 Tesseract，保存识别文字和字框。使用 `--language eng` 或 `--language chi_sim` 可以指定语言。用 `--ocr-pages 3,7` 补查指定页，原生文字健康时仍作为主文本。用 `--no-ocr` 时，有内容但文字不全的页返回 `needs_review`。

命令返回 0 表示操作成功，其中可以有待复核项。返回 1 时，输出中有页或表格提取失败，或者引文回查未通过。返回 2 表示输入或命令错误。
