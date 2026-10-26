"""Check an independently installed wheel, both command names and protected bytes."""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
from verify_release import business_manifest

PROJECT = Path(__file__).resolve().parents[1]


def check():
    import dataspace_baselines

    root = Path(dataspace_baselines.__file__).resolve().parent
    assert root != PROJECT / "src/dataspace_baselines"
    assert root.is_relative_to(PROJECT)
    manifest = json.loads((PROJECT / "provenance/extraction-manifest.json").read_text())
    assert len(manifest["files"]) == 22
    provenance = {p.relative_to(PROJECT).as_posix(): p.read_bytes() for p in (PROJECT / 'provenance').glob('*.json')}
    entries = business_manifest(provenance, {e['destination']: (root / Path(e['destination']).relative_to('src/dataspace_baselines')).read_bytes() for e in manifest['files']})
    assert dataspace_baselines.__version__ == importlib.metadata.version('eviweave') == '0.1.1'
    for entry in entries:
        p = root / Path(entry["destination"]).relative_to("src/dataspace_baselines")
        assert hashlib.sha256(p.read_bytes()).hexdigest() == entry["release_sha256"]
        if entry["policy"] == "verbatim":
            assert entry["release_sha256"] == entry["source_sha256"]
    assert importlib.metadata.requires("eviweave") == ["PyYAML<7,>=6"]
    commands = []
    for entry in ["eviweave", "dataspace-baselines"]:
        for arguments in [
            ["--help"],
            ["--version"],
            ["list"],
            ["doctor", "--help"],
            ["run", "--help"],
            ["batch", "--help"],
        ]:
            cmd = [str(Path(sys.executable).with_name(entry)), *arguments]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, (cmd, result.stderr)
            if arguments == ['--version']:
                assert result.stdout.strip() == 'eviweave 0.1.1'
            commands.append({"command": cmd, "returncode": 0, "stdout": result.stdout})
    return {
        "status": "PASS",
        "python": sys.version,
        "module": str(root),
        "protected_files": 17,
        "release_files": 22,
        "commands": commands,
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.write_text(json.dumps(check(), ensure_ascii=False, indent=2) + "\n")
