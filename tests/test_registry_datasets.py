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
"""Unit tests for Data Registry dataset selection / upload.

The DataRobot boundary is mocked; no live platform calls are made. Importing
``forecastic.api`` needs the deploy-time deployment/scoring-dataset ids, so we
inject dummy values before import and skip the module if it still can't load
(e.g. no stack settings file in a bare CI checkout).
"""

import os
from typing import Any
from unittest.mock import Mock, patch

import pandas as pd
import pytest

os.environ.setdefault("FORECAST_DEPLOYMENT_ID", "test-deployment-id")
os.environ.setdefault("FORECAST_SCORING_DATASET_ID", "test-scoring-dataset-id")

try:
    from forecastic import api
    from forecastic.schema import DataRegistryDataset

    _IMPORT_ERROR: Exception | None = None
except Exception as e:  # pragma: no cover - environment dependent
    api = None  # type: ignore[assignment]
    _IMPORT_ERROR = e

pytestmark = pytest.mark.skipif(
    api is None,
    reason=f"forecastic.api could not be imported: {_IMPORT_ERROR}",
)


def _make_dataset(
    *,
    id: str,
    name: str,
    size: int | None,
    is_snapshot: bool = True,
    created_at: str = "2024-01-02T03:04:05",
) -> Mock:
    ds = Mock()
    ds.id = id
    ds.name = name
    ds.size = size
    ds.is_snapshot = is_snapshot
    ds.created_at = created_at
    return ds


class TestListRegistryDatasets:
    """`list_registry_datasets` filtering + mapping."""

    def test_filters_and_maps(self) -> None:
        small = _make_dataset(id="d1", name="small", size=5 * 1024 * 1024)
        oversized = _make_dataset(
            id="d2", name="huge", size=api.REGISTRY_DATASET_SIZE_CUTOFF + 1
        )
        not_snapshot = _make_dataset(
            id="d3", name="dynamic", size=1024, is_snapshot=False
        )
        no_size = _make_dataset(id="d4", name="empty", size=None)

        with patch.object(
            api.Dataset,
            "iterate",
            return_value=iter([small, oversized, not_snapshot, no_size]),
        ) as mock_iterate:
            result = api.list_registry_datasets(limit=50)

        # Only the small snapshot survives the filter.
        assert [d.id for d in result] == ["d1"]
        assert result[0] == DataRegistryDataset(
            id="d1", name="small", created="2024-01-02", size="5.0 MB"
        )

        # Filtered to the app's use case, failed datasets excluded.
        _, kwargs = mock_iterate.call_args
        assert kwargs["filter_failed"] is True
        assert kwargs["use_cases"] == [api.app_settings.use_case_id]

    def test_limit_caps_usable_results(self) -> None:
        # limit bounds the number of usable datasets returned (iterate's own limit is
        # only a page size), so a smaller limit must win over the available count.
        many = [
            _make_dataset(id=f"d{i}", name=f"ds{i}", size=1024 * 1024) for i in range(5)
        ]
        with patch.object(api.Dataset, "iterate", return_value=iter(many)):
            result = api.list_registry_datasets(limit=2)
        assert [d.id for d in result] == ["d0", "d1"]


class TestGetScoringDataActiveDataset:
    """`get_scoring_data` honours the active dataset id."""

    def setup_method(self) -> None:
        api._get_scoring_data.cache_clear()

    def teardown_method(self) -> None:
        api._get_scoring_data.cache_clear()

    def _patch_get(self) -> Any:
        df = pd.DataFrame({"a": [1, 2]})
        dataset = Mock()
        dataset.get_as_dataframe.return_value = df
        return patch.object(api.dr.Dataset, "get", return_value=dataset)

    def test_uses_active_dataset_when_provided(self) -> None:
        with self._patch_get() as mock_get:
            api.get_scoring_data(None, active_dataset_id="active-123")
        mock_get.assert_called_once_with("active-123")

    def test_falls_back_to_configured_dataset(self) -> None:
        with self._patch_get() as mock_get:
            api.get_scoring_data(None, active_dataset_id=None)
        mock_get.assert_called_once_with(api.scoring_dataset_id)


class TestUploadDatasetToRegistry:
    """`upload_dataset_to_registry` creates + names + attaches the dataset."""

    def test_creates_and_maps(self) -> None:
        created = _make_dataset(id="new-1", name="tmpXXXX.csv", size=2 * 1024 * 1024)

        def _apply_modify(name: str | None = None, **_: Any) -> None:
            created.name = name

        created.modify.side_effect = _apply_modify

        with patch.object(
            api.Dataset, "create_from_file", return_value=created
        ) as mock_create:
            result = api.upload_dataset_to_registry(b"a,b\n1,2\n", "sales.csv")

        # Created from a temp file and attached to the app's use case.
        _, kwargs = mock_create.call_args
        assert kwargs["use_cases"] == [api.app_settings.use_case_id]
        assert kwargs["file_path"].endswith(".csv")

        # Renamed to the original filename and mapped to the API model.
        created.modify.assert_called_once_with(name="sales.csv")
        assert result == DataRegistryDataset(
            id="new-1", name="sales.csv", created="2024-01-02", size="2.0 MB"
        )

    def test_rejects_oversized_upload(self) -> None:
        oversized = b"x" * (api.REGISTRY_DATASET_SIZE_CUTOFF + 1)
        with patch.object(api.Dataset, "create_from_file") as mock_create:
            with pytest.raises(ValueError):
                api.upload_dataset_to_registry(oversized, "big.csv")
        mock_create.assert_not_called()
