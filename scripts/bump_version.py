from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("usage: python scripts/bump_version.py X.Y.Z")
        return 1
    version = argv[0].strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"invalid version: {version!r}")
        return 1

    version_path = REPO_ROOT / "src" / "rocky" / "version.py"
    pyproject_path = REPO_ROOT / "pyproject.toml"
    readme_path = REPO_ROOT / "README.md"

    version_path.write_text(
        'from __future__ import annotations\n\n"""Single source of truth for Rocky release metadata."""\n\nVERSION = __version__ = "{}"\n\n__all__ = ["VERSION", "__version__"]\n'.format(version),
        encoding="utf-8",
    )

    py_text = pyproject_path.read_text(encoding="utf-8")
    py_text = re.sub(r'(?m)^version = ".*?"$', f'version = "{version}"', py_text, count=1)
    pyproject_path.write_text(py_text, encoding="utf-8")

    readme_text = readme_path.read_text(encoding="utf-8")
    readme_text = re.sub(r'(?m)^Active development\. v\d+\.\d+\.\d+\.', f'Active development. v{version}.', readme_text, count=1)
    readme_path.write_text(readme_text, encoding="utf-8")
    print(f"bumped Rocky to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
