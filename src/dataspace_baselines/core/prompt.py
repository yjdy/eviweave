from __future__ import annotations

from pathlib import Path

from .task import TaskSpec


PROMPT_CONTRACT_VERSION = "relation-v6"


def relation_task_prompt(
    task: TaskSpec,
    *,
    workspace_path: str | Path,
    output_path: str | Path,
) -> str:
    """The common user-level contract used in native-harness evaluation."""

    return f"""Task ID: {task.task_id}

Question:
{task.question}

All task input artifacts are located under:
{workspace_path}

Solve the task using only those artifacts. Internet access is unavailable.
The preinstalled generic data-processing utilities are documented at
/opt/dataspace/TOOLS.md. Package installation is unavailable.
Write the complete requested relation to:
{output_path}

The answer must be a rectangular UTF-8 CSV with one header row. A header-only
CSV is valid when the requested result is empty. Return all requested rows and
columns; do not replace the CSV with a prose answer.

Before computing, establish the requested result schema from the question.
Use referenced videos and documents to determine data scope, field meanings,
metrics, units, time windows, filters, ordering, and tie rules. Preserve columns
explicitly requested by the question even if a video leaves them unchecked.
When the question explicitly delegates the output columns to a video or
document, follow that specified export configuration. Referencing a video's
batch criteria does not by itself delegate the output columns to that video.
Distinguish saved/current settings from drafts, historical settings, and
unrelated panels.

In your existing execution notes, keep a concise output-column checklist:
the output columns and corresponding source fields; the evidence for choosing
them; and any source conflicts with the interpretation you adopted. If there
are no conflicts, explicitly write "none". Keep this explanation outside the CSV.

Apply these rules when building the result:
- Output only the requested columns. Identifiers, ranks, filter values, sort
  keys, and explanatory attributes belong in the CSV only when requested.
  Determine the row meaning before writing: scalar aggregates without grouping
  normally form one row with one column per requested statistic, unless a
  different layout is specified.
- Distinguish records indexed by time from names, metrics, counts, or boolean
  answers selected under a time condition. For records indexed by time, decide
  whether the date is part of the requested record identity using the question
  and context. For the latter, do not add a date column merely because time is
  used to filter, select the latest, sort, or compute the answer.
- Treat date requirements according to their role. "List end dates and the
  corresponding amounts" requires a date column. "Output only the amounts,
  ordered by date ascending" uses the date only for sorting. For ambiguous
  wording such as "list records by date" or "show the latest record", use
  context to decide whether the date is part of the requested result and
  record the evidence; do not automatically add a date column to every result.
- Choose the exact source field for the requested kind of name or identifier.
  Full names, product short names, and trading/security abbreviations can be
  different fields with different values. Resolve this using the task's field
  labels and definitions rather than treating the fields as interchangeable.
  Preserve extracted text values, including punctuation, parentheses, and
  suffixes; do not paraphrase or add descriptive words to a result cell.
- Keep the requested metric, record population, and reporting period tied to
  their specified sources. A similarly named metric from a ranking table or
  another report is not a substitute merely because it covers more records.
  Verify join keys and retain every qualifying tie and required duplicate.
- Before aggregating, identify what is being counted. For questions asking
  how many companies, count distinct companies using a stable company
  identifier. For questions asking how many filings, occurrences, or records,
  count qualifying records. Do not automatically deduplicate rows merely
  because their output values are identical.
- Apply missing-value rules to the designated fields. Do not fill a missing
  field from a biography, another metric, or a different period just to make
  a record qualify unless the task or its rules authorize that derivation.
  Use prose as a value source when the task artifacts designate it as such.
  Approximate or bounded quantities do not supply exact values when the task
  requires explicitly stated exact numbers. Record unresolved source gaps in
  execution notes without inventing values or adding diagnostic CSV columns.

Do not finish the task until the exact file path above exists. Before ending,
verify that its filename is exactly prediction.csv, that it is readable as a
rectangular CSV, and that it contains the complete final relation. Re-read the
written file with local code and compare its column names and column count
with your output-column checklist. Correct any mismatch within this session.
This consistency check does not establish that the interpretation is correct.
Also check selected source fields, row meaning, filters, units, text preservation,
coverage, ordering, and ties against the result schema established from the task.
Keep supporting calculations and uncertainty explanations in execution notes. A prose
response or a differently named file does not count as submission. Once the
verified prediction.csv is ready, terminate the session."""
