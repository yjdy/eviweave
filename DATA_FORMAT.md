# 数据格式与自定义数据集

本文说明 EviWeave 当前 V6 实现读取的任务格式，以及如何用自己的资料运行单个任务。准备数据只需一个任务说明文件和一个资料目录，不需要把所有资料转换为同一种表格。

当前支持范围：

| 用途 | 当前入口与限制 |
|---|---|
| 自定义单题推理 | 包内 `run --task-dir`，读取本文格式，生成预测和运行日志 |
| DataSpace 统一实验 | `scripts/experiment.sh`，固定题号范围为1–410，默认选择公开60题，使用已锁定的 DataSpace 评分资源 |
| 自定义答案评分 | 需另外准备参考答案和评分逻辑；当前统一验证工具不支持注册任意自定义评分资源 |

**自定义任务请先使用本文的单题入口。** 将自定义目录传给统一实验的 `--dataset`，并不代表它已经支持该数据集的选题、评分和续跑。复用 DataSpace 的题号还可能让自定义答案被拿去与无关的官方参考答案比较。

## 1. 目录结构

建议把私有数据放在项目已忽略的 `data/` 下，与原 DataSpace 数据分开：

```text
data/my-dataset/
└── input/
    ├── task_1/
    │   ├── task.json
    │   └── context/
    │       ├── knowledge.md       # 可选：字段、单位、关联及资料说明
    │       ├── csv/
    │       │   └── records.csv
    │       ├── json/             # 以下子目录均按需设置
    │       ├── db/
    │       ├── doc/
    │       ├── image/
    │       └── video/
    └── task_2/
        ├── task.json
        └── context/
            └── ...
```

每个任务必须有 `task.json` 和 `context/`。`csv/`、`doc/` 等子目录不是强制分类；也可以直接把文件放在 `context/` 下。没有资料的纯问题任务仍需创建空的 `context/`。

本文使用无前导零的正整数目录名 `task_1`、`task_2`，并让目录名与 `task_id` 一致。这样便于以后接入其他入口。单题加载器本身不要求完整的 `input/` 父目录，只要求 `--task-dir` 指向包含上述两个成员的任务目录。

各任务的 `task_id` 应唯一，并使用安全的文件名形式；不要使用路径分隔符、`..` 或控制字符。为保持入口兼容，推荐统一采用 `task_N`。

## 2. task.json

使用 UTF-8 JSON 对象。需要提供以下两个非空字符串：

| 字段 | 含义 |
|---|---|
| `task_id` | 任务标识，例如 `task_1` |
| `question` | 完整的分析要求；会作为任务问题提供给模型 |

最小示例：

```json
{
  "task_id": "task_1",
  "question": "读取 csv/records.csv，仅选择 status 为 confirmed 的记录。输出 record_id、amount 两列，按 record_id 字符串升序排列。保留 record_id 的前导零和 amount 的原始小数文本，不增加其他列。"
}
```

当前加载器只读取这两个字段。额外增加 `split`、`tags`、`expected_columns`、`gold_path` 等字段，不会自动建立数据划分、输出约束或评分规则。需要模型知道的要求，应明确写进 `question` 或 `context/` 中的资料说明。

问题描述建议交代：

- 每行结果代表什么对象，需要哪些输出列。
- 使用哪些资料，字段之间如何关联，采用什么单位和时间范围。
- 筛选、排序、去重、并列和缺失值的处理方式。
- 数字是否允许转换或舍入，文本和标识符需要保留哪些格式。

资料引用使用 `csv/records.csv` 这类相对路径，避免写本机绝对路径。

## 3. context/ 中的资料

求解时只有当前任务的 `context/` 被作为资料目录只读挂载到 `/mnt/workspace`。例如，`context/csv/records.csv` 在沙箱内对应 `/mnt/workspace/csv/records.csv`。任务说明中的 `question` 会进入提示词；同级其他文件不会因此自动成为可读资料。

| 资料类型 | 准备建议 |
|---|---|
| CSV、TSV、文本、Markdown | 优先使用UTF-8；说明分隔符、列含义、空值约定和单位 |
| JSON | 使用有效JSON；说明记录数组的位置及字段含义，需保留前导零的标识符使用字符串 |
| SQLite | 提供完整数据库文件；先结束写入并处理WAL等旁文件，确保只读打开即可读取完整数据 |
| PDF及其他文档 | 说明目标章节、表格和单位；扫描件可能需要OCR，文件可读不等于能准确提取 |
| 图片、视频 | 使用工作台现有工具能够读取的格式；必要时说明时间位置、画面含义和目标字段 |

加载器不按扩展名限制资料，但这不表示工作台能解析任意格式。可用工具见 [工作台工具清单](docker/workbench/TOOLS.md)，实际环境以其 `/opt/dataspace/TOOLS.md` 为准。网络访问和安装新包不可用；不要让任务依赖在线下载、外部数据库服务、登录态或临时链接。

所有需要的资料都应放在本题 `context/` 内。共享资料需分别准备到各题目录，不能假定模型可以读取相邻任务、项目根目录或本机其他位置。推荐只使用普通文件和目录，避免符号链接以及指向任务目录之外的引用。

参考答案、评分脚本、历史预测及凭据应保存在 `context/` 之外，例如项目私有的 `.local/my-dataset-evaluation/`；放入 `context/` 就会暴露给求解过程。

## 4. 一个完整的输入与输出示例

将以下内容保存为上述示例的 `context/csv/records.csv`：

```csv
record_id,status,amount
0001,confirmed,12.3400
0002,pending,7.00
0003,confirmed,0.10
```

结合第2节的 `task.json`，期望输出为：

```csv
record_id,amount
0001,12.3400
0003,0.10
```

输入CSV中的前导零和末尾小数零属于本题要求保留的文本。生成或读取数据时应避免先转成整数、浮点数，导致原始格式丢失。期望输出仅用于宿主侧核对，不要作为输入文件交给模型。

仓库还提供了两道无真实业务数据的[合成任务示例](examples/synthetic/README.md)，可参考其目录和原始资料组织方式。

## 5. 运行自定义单题

先按 [README](README.zh-CN.md#统一实验流程) 完成安装、配置有效模型端点及准备历史运行时。以下命令从项目根目录执行，使用默认的私有配置；若使用其他配置，替换 `--config` 路径即可。

先做无模型环境检查：

```sh
mkdir -p .audit/tmp
.venv-experiment/bin/python -B -m dataspace_baselines doctor \
  --offline --config .local/config-v6/codex.yaml \
  --temp-dir .audit/tmp
```

这只检查本地环境，不验证数据语义或服务鉴权。确认本题两个必需成员存在，资料可由现有工具读取，输出要求也已写清楚。

在终端自行设置专用 `EVIWEAVE_API_KEY` 后，以下命令会调用真实模型并产生费用：

```sh
.venv-experiment/bin/python -B -m dataspace_baselines run \
  --harness codex \
  --task-dir data/my-dataset/input/task_1 \
  --config .local/config-v6/codex.yaml \
  --run-dir .audit/my-dataset-task1-run1
```

包内入口直接读取终端环境，不会自动加载 `.local/env.sh`；若需要该文件的设置，先执行 `source scripts/env.sh`。仅使用 `EVIWEAVE_API_KEY`，不要把key写进 `task.json`、资料或模型YAML。

每次运行使用新的输出目录，结果包括：

```text
.audit/my-dataset-task1-run1/
├── run.json                   # 状态、耗时、工具动作和已记录用量
├── events.jsonl               # 运行开始、校验及结束事件
├── stdout.log
├── stderr.log
├── runtime/home/              # 当前任务的独立HOME
└── output/
    └── prediction.csv         # 成功提交时的正式预测
```

该单题入口不自动创建统一实验的 `experiment.json`、归集目录或评分报告，不能将其输出直接交给 `scripts/experiment.sh verify` 或 `--resume`。需要再次尝试时显式使用新输出目录，并保留前一次记录。

## 6. 预测CSV与评分

模型须在沙箱中写入 `/mnt/output/prediction.csv`，对应宿主运行目录中的 `output/prediction.csv`：

- 使用UTF-8和逗号分隔，第一行为表头，至少有一列。
- 每个数据行的列数与表头一致；含逗号、引号或换行的单元格应按CSV规则引用。
- 空结果保留表头，允许没有数据行；完全空的文件无效。
- 输出列、数值精度、单位、空值及行序应符合任务要求。

可使用沙箱内预装的无损导出器，由 `{"columns": [...], "rows": [...]}` 生成CSV。该JSON是可选的输出中间格式，不是数据集输入要求：

```json
{
  "columns": ["record_id", "amount"],
  "rows": [["0001", "12.3400"], ["0003", "0.10"]]
}
```

模型在沙箱内调用：

```sh
python3 /mnt/runtime/dataspace-export.py --input /mnt/output/final_table.json
```

导出器将JSON `null` 写为空单元格，布尔值写为 `true`/`false`；有限JSON数字按精确十进制解析，字符串原样保留。不要把对象或数组放进单元格，也不要期望导出器恢复此前已丢失的精度。

`submitted` 只说明执行与CSV格式校验通过，不代表答案正确。自定义评分需要在宿主侧使用独立参考答案，按预先定义的列映射、类型、数值容差、空值、重复行和排序规则比较结果。

当前统一实验和 `tools/standalone.py score` 使用已锁定的 DataSpace 评分材料，不能把自定义gold直接替换进去，也不要修改原评分锁来绕过检查。自定义任务得分应单独报告，不能混入DataSpace成绩。

## 7. 发布与复现

`data/`、`.local/` 和 `.audit/` 均不进入Git或发行包。本文示例为人工数据；使用自己的数据时遵守相应使用和分发条款。

保留每次运行的原始输入、模型配置和输出记录。更换资料、问题、模型、预算或工具版本后，作为新的实验记录，不覆盖旧结果。本文只描述现有实现，不改变冻结V6的提示词、运行器或评分政策。
