"""MIT; Jun Yuan / 袁珺. Deterministic historical evidence and redaction helpers."""

from __future__ import annotations
import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[1]
SCHEMA = 1
TASKS = tuple(f"task_{n}" for n in range(1, 411))
RETRIES = frozenset({22, 23, 207, 273})
TRACE_ATTEMPTS = ((33, 1), (328, 1), (35, 1), (391, 1), (23, 1), (23, 2))
MISSING_USAGE = frozenset(
    {("task_22", "run_1"), ("task_23", "run_1"), ("task_207", "run_1")}
)
MANIFEST_SHA = "bd031c52e3f7322d551d105dfe03f78e847ad8e2e5984e5b16c51632af907236"
FINGERPRINT = "367c7815e55b02c3b33ecd4422faddfdc24cc042767a87726b3547052584d396"
EVALUATOR_SHA = "9eeb1edcc72d963610f497ebf98287887659f52656092ada0a55290a066bfbc8"
CONFIG_SHA = "0efe57a7a9ea4bf82a2ac994b5ec1d29ba9997882b0403e744cccb8d5e40ce54"
OFFICIAL_HASHES = {
    "leaderboard.html": "0238a2fc1d7d543bcdd28b6fb60a7f2e8369b71454627e4969bdebe105c9cd35",
    "leaderboard.js": "15c1c031394b92fc474757d0e206ce9be7be7004632a3ecb371d72f364d9bc27",
}
ARCHIVE_NAME = "eviweave-v0.1.0-v6-evidence.zip"
POLICY = "fixed-four-recovery-v1"
SECRET_KEYS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "password",
    "passwd",
    "clientsecret",
    "authorization",
    "bearertoken",
    "token",
    "secret",
}
ID_KEYS = {
    "requestid",
    "sessionid",
    "threadid",
    "responseid",
    "traceid",
    "spanid",
    "correlationid",
}
BASE_KEYS = (
    "task_id",
    "harness",
    "model",
    "status",
    "return_code",
    "usage",
    "wall_time_seconds",
    "model_turns",
    "tool_actions",
    "error",
    "prediction_path",
)


class EvidenceError(ValueError):
    pass


class MissingMaterial(EvidenceError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode()


def loads(data):
    def reject(value):
        raise EvidenceError("non-finite JSON number")

    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "duplicate JSON key")
            out[key] = value
        return out

    try:
        return json.loads(data, parse_constant=reject, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise EvidenceError("invalid JSON") from exc


def json_lines(data):
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise EvidenceError("invalid UTF-8 JSONL") from exc
    require(bool(lines) and all(x.strip() for x in lines), "empty JSONL line or log")
    values = [loads(x) for x in lines]
    require(all(isinstance(x, dict) for x in values), "JSONL events must be objects")
    return values


def output_path(value, sources=()):
    path = Path(value).absolute()
    require(
        not any(p.is_symlink() for p in [path, *path.parents]),
        "output contains symlink",
    )
    path = path.resolve()
    require(
        path != PROJECT and path.is_relative_to(PROJECT),
        "output must be a child of project",
    )
    for source in sources:
        source = Path(source).resolve()
        require(
            not (
                path == source
                or path.is_relative_to(source)
                or source.is_relative_to(path)
            ),
            "output and sources must be disjoint",
        )
    require(
        not path.exists() or (path.is_dir() and not any(path.iterdir())),
        "output must be new or empty",
    )
    return path


def selected(task):
    return "run_2" if int(task.removeprefix("task_")) in RETRIES else "run_1"


def expected_attempts():
    return [
        (t, f"run_{n}")
        for t in TASKS
        for n in ([1, 2] if int(t[5:]) in RETRIES else [1])
    ]


def indexed(records, keys):
    require(isinstance(records, list), "record collection must be a list")
    out = {}
    for record in records:
        require(isinstance(record, dict), "record must be an object")
        key = tuple(record.get(k) for k in keys)
        require(key not in out, "duplicate record")
        out[key] = record
    return out


def csv_rows(data):
    import sys

    csv.field_size_limit(min(sys.maxsize, 128 * 1024 * 1024))
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
        require(
            rows and rows[0] and all(len(r) == len(rows[0]) for r in rows),
            "invalid rectangular CSV",
        )
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise EvidenceError("invalid CSV encoding or structure") from exc


class Source:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}
        if not self.root.is_dir():
            raise MissingMaterial("source directory missing")

    def read(self, relative):
        p = self.root / relative
        require(p.resolve().is_relative_to(self.root), "source path escapes root")
        require(
            not any(
                x.is_symlink() for x in [p, *p.parents] if x.is_relative_to(self.root)
            ),
            "source links are not accepted",
        )
        if not p.is_file():
            raise MissingMaterial("required source file missing: " + str(relative))
        data = p.read_bytes()
        sha = digest(data)
        if relative in self.files:
            require(
                self.files[relative]["sha256"] == sha, "source changed during build"
            )
        self.files[relative] = {"sha256": sha, "bytes": len(data)}
        return data

    def json(self, relative):
        return loads(self.read(relative))

    def unchanged(self):
        for rel, item in list(self.files.items()):
            require(digest(self.read(rel)) == item["sha256"], "source changed")


UUID = re.compile(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.I)
REQUEST = re.compile(
    r"\b(?:req|request|resp|thread|session|chatcmpl|call)[_-][A-Za-z0-9_-]{8,}\b", re.I
)
TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
BEARER = re.compile(r"(?i)(\bBearer\s+)([^\s\x22\x27\\,;}\]]+)")
ASSIGN = re.compile(
    r"""(?ix)(\b(?:[a-z0-9_]*(?:api_key|access_token|refresh_token|client_secret)|api-?key|password|passwd|authorization|token|secret)[\\"']*\s*[:=]\s*[\\"']*)([^\s\x22\x27\\,;}\]]+)"""
)
QUOTED_SECRET = re.compile(
    r"""(?ix)(\b(?:[a-z0-9_]*(?:api_key|access_token|refresh_token|client_secret)|api-?key|password|passwd|authorization|token|secret)[\\"']*\s*[:=]\s*)(\\?["'])(.*?)(\2)""",
    re.S,
)
AUTH_HEADER = re.compile(
    r"""(?ix)(\bAuthorization[\\"']*\s*[:=]\s*[\\"']*(?:(?:Bearer|Basic)\s+)?)([^\s\x22\x27\\,;}\]]+)"""
)
ID_ASSIGN = re.compile(
    r"""(?ix)(\b(?:x[-_]request[-_]id|request[-_]id|session[-_]id|thread[-_]id|response[-_]id|trace[-_]id|span[-_]id)[\\"']*\s*[:=]\s*[\\"']*)([^\s\x22\x27\\,;}\]]+)"""
)
HOST = re.compile(r"/(?:home/[A-Za-z0-9_.-]+|root)(?=/|\b)")


class Redactor:
    def __init__(self, root, identity):
        self.aliases = {str(Path(root).resolve()): "<EXPERIMENT_ROOT>"}
        self.ids = {}
        self.known = set()
        self.edits = []
        self.aliases.update({str(root).replace("/", "\\/"): "<EXPERIMENT_ROOT>"})
        for key in ["openai_base_url", "anthropic_base_url"]:
            url = identity.get("model", {}).get(key)
            if url:
                parts = urlsplit(url)
                origin = parts.scheme + "://" + parts.netloc
                for value, replacement in [
                    (url, "https://example.invalid/v1"),
                    (origin, "https://example.invalid"),
                ]:
                    self.aliases[value] = replacement
                    self.aliases[value.replace("/", "\\/")] = replacement
        for key, value in self.aliases.items():
            if key != value:
                self.known.add(key)

    def identifier(self, value):
        if value not in self.ids:
            self.ids[value] = f"<ID_{len(self.ids)+1:04d}>"
        self.known.add(value)
        return self.ids[value]

    def change(self, rule, location, value, replacement):
        if value == replacement:
            return replacement
        self.edits.append({"rule": rule, "location": location})
        return replacement

    def text(self, text, location):
        for old, new in sorted(self.aliases.items(), key=lambda x: (-len(x[0]), x[0])):
            if old in text:
                count = text.count(old)
                text = text.replace(old, new)
                self.edits.extend(
                    {"rule": "declared-source-or-endpoint", "location": location}
                    for _ in range(count)
                )
        text = HOST.sub(
            lambda m: self.change("host-home", location, m[0], "<HOST_HOME>"), text
        )
        text = ID_ASSIGN.sub(
            lambda m: (
                m[1]
                + self.change("identifier-text", location, m[2], self.identifier(m[2]))
                if not m[2].startswith("<ID_")
                else m[0]
            ),
            text,
        )
        text = UUID.sub(
            lambda m: self.change("identifier", location, m[0], self.identifier(m[0])),
            text,
        )
        text = REQUEST.sub(
            lambda m: self.change("identifier", location, m[0], self.identifier(m[0])),
            text,
        )

        def secret(m):
            value = m[0]
            self.known.add(value)
            return self.change("credential-token", location, value, "<REDACTED>")

        def quoted(m):
            value = m[3]
            if value == "<REDACTED>":
                return m[0]
            self.known.add(value)
            return (
                m[1]
                + m[2]
                + self.change("credential-quoted", location, value, "<REDACTED>")
                + m[4]
            )

        text = QUOTED_SECRET.sub(quoted, text)

        def auth(m):
            if m[2] == "<REDACTED>":
                return m[0]
            self.known.add(m[2])
            return m[1] + self.change("authorization", location, m[2], "<REDACTED>")

        text = AUTH_HEADER.sub(auth, text)
        text = TOKEN.sub(secret, text)

        def bearer(m):
            value = m[2]
            if value == "<REDACTED>":
                return m[0]
            self.known.add(value)
            return m[1] + self.change("bearer", location, value, "<REDACTED>")

        text = BEARER.sub(bearer, text)

        def assignment(m):
            value = m[2]
            if value in ["<REDACTED>", "Bearer", "Basic"] or value.startswith("<ID_"):
                return m[0]
            self.known.add(value)
            return m[1] + self.change(
                "credential-assignment", location, value, "<REDACTED>"
            )

        return ASSIGN.sub(assignment, text)

    def value(self, value, location="$"):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                normalized = re.sub("[^a-z0-9]", "", key.lower())
                loc = location + "/" + key
                clean_key = self.text(key, loc + "/key")
                require(clean_key not in result, "redacted JSON key collision")
                if normalized in SECRET_KEYS and item not in (None, ""):
                    require(isinstance(item, str), "unsupported credential field type")
                    self.known.add(item)
                    result[clean_key] = self.change(
                        "credential-field", loc, item, "<REDACTED>"
                    )
                elif normalized in ID_KEYS and isinstance(item, str):
                    result[clean_key] = self.change(
                        "identifier-field", loc, item, self.identifier(item)
                    )
                else:
                    result[clean_key] = self.value(item, loc)
            return result
        if isinstance(value, list):
            return [self.value(v, location + "/" + str(i)) for i, v in enumerate(value)]
        if isinstance(value, str):
            return self.text(value, location)
        return value
