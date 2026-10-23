# Reproduction profiles

`cpython312-linux-x86_64` is the CPython 3.12.3 reproduction profile;
`cpython313-linux-x86_64` is the separate CPython 3.13.16 compatibility profile.
These profiles register the verified host on 2026-10-23, including interpreter
binary, ABI, OS/kernel, Docker and Bubblewrap. They do not authenticate the
historical V6 host. A different host requires an explicitly reviewed new profile
and a new experiment directory; do not edit a lock to suppress a mismatch.

Each JSON profile contains exact dependency versions, official PyPI artifact URLs,
SHA-256 values and wheel member hashes. The adjacent requirements file is the
hashed installation lock. Versions come from previously verified build environments
and logs; pip 24.0 was registered from the existing CPython 3.12 host. Installation
checks existing content before changing it, downloads only these artifacts, uses
`--require-hashes`, and builds EviWeave with `--no-build-isolation --no-deps`.
The package's compatible runtime dependency remains PyYAML>=6,<7.

Historical runtime inventories (16 Codex and 18,516 Workbench entries) and
21/329 Python/system package inventories are locked separately. Complete imported
inventories remain private; their digests and the published package lists are
checked against actual runtime contents. Rebuilt Workbench is a distinct environment
with recorded differences and is not accepted by these historical-import profiles.
The installer never downloads Codex, builds Workbench, or restores an entire machine.

`ci-tools.txt` pins the same dependency versions and authenticated Linux x86_64
wheel artifacts for functional CI on Python3.10/3.12/3.13. This does not assert
that hosted CI matches the registered host binaries. `ci_check.py --locked` also
requires the selected local interpreter and installed toolchain content to match
its reproduction/compatibility profile. All builds disable build isolation.
Python3.10 functional CI additionally pins tomli2.0.1 (the verified setuptools
core minimum, also satisfying build's TOML-parser dependency); its wheel hash was
registered from official PyPI metadata for this CI extension, not from a historical
V6 Python3.10 environment. Python3.10 hosted execution is still unverified locally.
