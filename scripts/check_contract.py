"""Check that contracts/openapi.json is a valid OpenAPI document and still equals what scripts/gen_openapi.py generates.

Run from anywhere:
    python scripts/check_contract.py

Needs `pip install openapi-spec-validator`. Used by CI (.github/workflows/ci.yml). This only reads: it never rewrites the contract.
A failure means someone edited the JSON by hand, or changed the generator without regenerating; either needs human review
(see CLAUDE.md, API Contract Governance).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "openapi.json"
GENERATOR = ROOT / "scripts" / "gen_openapi.py"


def main() -> int:
    from openapi_spec_validator import validate

    committed = json.loads(CONTRACT.read_text(encoding="utf-8"))
    validate(committed)
    print(f"valid OpenAPI {committed['openapi']}: {len(committed['paths'])} paths, {len(committed['components']['schemas'])} schemas")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "generated.json"
        subprocess.run([sys.executable, str(GENERATOR), str(out)], check=True, stdout=subprocess.DEVNULL)
        generated = json.loads(out.read_text(encoding="utf-8"))

    # Compare parsed JSON, not bytes: line endings and the trailing newline differ between platforms.
    if generated != committed:
        paths = sorted(set(generated["paths"]) ^ set(committed["paths"]))
        print("DRIFT: contracts/openapi.json does not match the output of scripts/gen_openapi.py.")
        if paths:
            print("  paths present in only one of them:", ", ".join(paths))
        print("  Do not hand-edit the contract. Change the generator, get the amendment approved, then regenerate.")
        return 1
    print("contract equals the generator output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
