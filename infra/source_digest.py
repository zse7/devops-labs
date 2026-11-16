"""Digest только файлов Docker-сборки, без ключей и локального окружения."""

import hashlib
from pathlib import Path

root = Path(__file__).resolve().parent.parent
paths = [
    root / name
    for name in ("Dockerfile", ".dockerignore", "package.json", "package-lock.json")
]
paths.extend(sorted((root / "src").rglob("*.js")))
digest = hashlib.sha256()
for path in paths:
    content = path.read_bytes()
    digest.update(str(path.relative_to(root)).encode() + b"\0")
    digest.update(str(len(content)).encode() + b"\0" + content)
print(digest.hexdigest())
