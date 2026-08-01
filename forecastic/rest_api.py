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
from __future__ import annotations

import logging
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

sys.path.append("..")

from forecastic.api import (
    LLMNotAvailableException,
    get_app_settings,
    get_filters,
    get_formatted_predictions,
    get_llm_summary,
    get_runtime_attributes,
    get_scoring_data,
    get_scoring_dataset_versions,
    list_registry_datasets,
    share_access,
    upload_dataset_to_registry,
)
from forecastic.schema import (
    AppRuntimeAttributes,
    AppSettings,
    DataRegistryDataset,
    FilterSpec,
    ForecastSummary,
    MultiSelectFilter,
)

app = FastAPI()

# Dev: the Vite dev server (http://localhost:5173) calls this API cross-origin with
# credentials. Allow that explicit origin (never "*" while credentials are enabled).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/appSettings")
async def get_app_settings_endpoint() -> AppSettings:
    return get_app_settings()


@app.get("/runtimeAttributes")
async def get_runtime_attributes_endpoint() -> AppRuntimeAttributes:
    return get_runtime_attributes()


@app.get("/filters")
async def get_filters_endpoint() -> List[MultiSelectFilter]:
    return get_filters()


@app.get("/scoringData")
async def get_scoring_data_endpoint(
    filter_selection: Optional[List[FilterSpec]] = None,
    active_dataset_id: Optional[str] = None,
    version_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    return get_scoring_data(filter_selection, active_dataset_id, version_id)


@app.get("/scoringDataVersions")
async def get_scoring_data_versions_endpoint(
    active_dataset_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    return get_scoring_dataset_versions(active_dataset_id)


@app.get("/registryDatasets")
async def get_registry_datasets_endpoint(
    limit: int = 100,
) -> List[DataRegistryDataset]:
    return list_registry_datasets(limit)


@app.post("/datasets/upload")
async def upload_dataset_endpoint(
    file: UploadFile = File(...),
) -> DataRegistryDataset:
    file_bytes = await file.read()
    try:
        return upload_dataset_to_registry(
            file_bytes, file.filename or "uploaded_dataset"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail=str(e),
        )


@app.post("/predictions")
async def get_predictions_endpoint(
    scoring_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return get_formatted_predictions(scoring_data)


@app.post("/llmSummary")
async def get_llm_summary_endpoint(
    predictions: List[dict[str, Any]],
) -> ForecastSummary:
    try:
        return get_llm_summary(predictions)
    except LLMNotAvailableException:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="LLM service not available",
        )


@app.patch("/share")
async def share_endpoint(emails: List[str]) -> None:
    share_access(emails)


# Serve the built React SPA (frontend_react/react_src builds into forecastic/build).
# Mounted AFTER the API routes above so /appSettings etc. take precedence; html=True
# serves index.html for client-side routes (e.g. /explanations, /what-if).
_BUILD_DIR = Path(__file__).parent / "build"
if (_BUILD_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=_BUILD_DIR, html=True), name="spa")
else:
    logging.getLogger(__name__).warning(
        "React build not found at %s — run `yarn build` in frontend_react/react_src. "
        "API routes still work; the SPA will 404 until the build exists.",
        _BUILD_DIR,
    )
