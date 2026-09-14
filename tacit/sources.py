import os
from pathlib import Path

EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".csv"}
SKIP_DIRS = {"node_modules", "__pycache__", "vendor", "dist", "build"}
SKIP_NAMES = {"auth.json", "credentials.json", "secrets.json", "secrets.toml"}


def read_sources(root: Path, *, max_files=32, max_chars=64000, per_file=8000):
    root = root.resolve(strict=True)
    sources = []
    remaining = max_chars
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if not name.startswith(".")
            and name not in SKIP_DIRS
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(directory) / name
            if (
                name.startswith(".")
                or name in SKIP_NAMES
                or path.suffix.lower() not in EXTENSIONS
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    continue
                limit = min(per_file, remaining)
                with resolved.open(encoding="utf-8") as stream:
                    content = stream.read(limit + 1)
            except (OSError, UnicodeError):
                continue
            if not content.strip():
                continue
            sources.append(
                {
                    "path": str(path.relative_to(root)),
                    "content": content[:limit],
                    "truncated": len(content) > limit,
                }
            )
            remaining -= min(len(content), limit)
            if len(sources) >= max_files or remaining <= 0:
                return sources
    return sources
