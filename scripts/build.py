from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "_build" / "html"


def executable(name: str) -> str:
    """Find a command and provide a useful failure message."""
    candidates = [name, f"{name}.cmd"] if sys.platform == "win32" else [name]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    raise SystemExit(
        f"Required command '{name}' was not found. "
        "Install the project dependencies described in README.md."
    )


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)

    npx = executable("npx")
    jupyter = executable("jupyter")

    # Build the static MyST course website.
    run([npx, "--no-install", "myst", "build", "--html"])

    # Add a full JupyterLite application inside the MyST static output.
    run(
        [
            jupyter,
            "lite",
            "build",
            "--contents",
            "public-modules",
            "--output-dir",
            str(OUTPUT / "jupyterlite"),
        ]
    )

    # Prevent GitHub Pages/Jekyll from ignoring generated asset directories.
    (OUTPUT / ".nojekyll").touch()

    print(f"\nBuilt combined site at: {OUTPUT}")
    print("Run 'npm run serve' and open http://localhost:8000")


if __name__ == "__main__":
    main()
