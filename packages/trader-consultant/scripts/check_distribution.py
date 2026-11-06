"""Build and import the wheel away from the checkout, including license metadata."""

import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


with tempfile.TemporaryDirectory(prefix="trader-consultant-wheel-") as directory:
    dest = Path(directory)
    run("uv", "build", "--project", str(ROOT), "--out-dir", str(dest / "dist"))
    source = next((dest / "dist").glob("*.tar.gz"))
    with tarfile.open(source) as archive:
        names = archive.getnames()
        assert any(name.endswith("/docs/tools.md") for name in names)
        assert any(name.endswith("/docs/provenance.md") for name in names)
    wheels = list((dest / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("expected one wheel")
    run("uv", "venv", "--python", sys.executable, str(dest / "venv"))
    python = dest / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run("uv", "pip", "install", "--python", str(python), str(wheels[0]))
    run(
        str(python),
        "-I",
        "-c",
        """
from importlib.metadata import distribution
from pathlib import Path
import trader_consultant
from trader_consultant import RebalanceRequest, compute_rebalance
assert Path(trader_consultant.__file__).is_relative_to(Path(__import__('sys').prefix))
files = [str(f) for f in distribution('trader-consultant').files]
assert any(f.endswith('/LICENSE') for f in files)
assert any(f.endswith('/NOTICE') for f in files)
result = compute_rebalance(RebalanceRequest(holdings={'A': '100'}, targets={'A': '.5'}, cash='100'))
assert result['positions'][0]['trade_value'] == 0
print('clean wheel import, arithmetic and license metadata OK')
""",
        cwd=directory,
    )
