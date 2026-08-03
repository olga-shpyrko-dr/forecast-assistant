# Copyright 2024 DataRobot, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Builds the React SPA as part of the Pulumi resource graph, instead of requiring
a manual `yarn build`/`npm run build` step before `pulumi up`.

Only relevant when FRONTEND_TYPE=react — the Streamlit frontend needs no build step.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pulumi_command as command

from .settings_main import PROJECT_ROOT, project_name

FRONTEND_SOURCE_GLOBS = [
    "src/**/*",
    "public/**/*",
    "package.json",
    "package-lock.json",
    "index.html",
    "tsconfig*.json",
    "vite.config.*",
    "tailwind.config.*",
    "postcss.config.*",
    "eslint.config.*",
    "components.json",
    ".prettierrc*",
    ".npmrc",
]


def _hash_frontend_sources(frontend_dir: Path) -> str:
    """SHA-256 hash of all relevant frontend source files, used as a Command trigger
    so the build only re-runs when the frontend source actually changes."""
    h = hashlib.sha256()
    seen: set[Path] = set()
    paths: list[Path] = []
    for pattern in FRONTEND_SOURCE_GLOBS:
        for p in frontend_dir.glob(pattern):
            if p.is_file() and p not in seen:
                seen.add(p)
                paths.append(p)
    for p in sorted(paths):
        h.update(str(p.relative_to(frontend_dir)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def build_frontend() -> command.local.Command:
    """Build the React SPA before the ApplicationSource is packaged.

    Callers should make the ApplicationSource's `files` depend on this command's
    completion (e.g. via `.stdout.apply(...)`) to guarantee build-then-package
    ordering in the resource graph.
    """
    frontend_dir = PROJECT_ROOT / "frontend_react" / "react_src"
    build_command = " && ".join(
        [
            f"cd {frontend_dir}",
            "npm ci",
            "npm run build",
        ]
    )
    return command.local.Command(
        f"Forecasting Assistant Build Frontend [{project_name}]",
        create=build_command,
        triggers=[_hash_frontend_sources(frontend_dir)],
    )
