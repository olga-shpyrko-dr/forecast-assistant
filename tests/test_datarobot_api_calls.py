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

import os
from typing import Any
from unittest.mock import Mock, patch

import pytest

from utils.datarobot_api_helpers import get_application_source_resources


class TestDataRobotAPI:
    """Test suite for DataRobot API utility functions"""

    def test_get_application_source_resources_missing_endpoint(self) -> None:
        """Test that missing DATAROBOT_ENDPOINT raises RuntimeError"""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(
                RuntimeError,
                match="DATAROBOT_ENDPOINT environment variable is required",
            ):
                get_application_source_resources("test-source-id")

    def test_get_application_source_resources_missing_token(self) -> None:
        """Test that missing DATAROBOT_API_TOKEN raises RuntimeError"""
        with patch.dict(
            os.environ, {"DATAROBOT_ENDPOINT": "https://test.datarobot.com"}, clear=True
        ):
            with pytest.raises(
                RuntimeError,
                match="DATAROBOT_API_TOKEN environment variable is required",
            ):
                get_application_source_resources("test-source-id")

    @patch("utils.datarobot_api_helpers.requests.get")
    def test_get_application_source_resources_success(self, mock_get: Any) -> None:
        """Test successful API call returns resources"""
        # Mock environment variables
        with patch.dict(
            os.environ,
            {
                "DATAROBOT_ENDPOINT": "https://test.datarobot.com",
                "DATAROBOT_API_TOKEN": "test-token",
            },
        ):
            # Mock successful API response based on actual DataRobot API structure
            mock_response = Mock()
            mock_response.json.return_value = {
                "id": "686d4e1d49b3f25cf01a4c49",
                "name": "Forecasting Assistant Application Source",
                "latestVersion": {
                    "id": "686d4e1e6a752147a67b80aa",
                    "version": 1,
                    "resources": {
                        "resourceLabel": "cpu.small",
                        "memoryRequest": 268435456,
                        "memoryLimit": 536870912,
                        "cpuRequest": 0.5,
                        "cpuLimit": 1,
                        "replicas": 1,
                        "sessionAffinity": False,
                        "serviceWebRequestsOnRootPath": True,
                    },
                    "createdAt": "2025-07-08 16:58:14.675000",
                    "updatedAt": "2025-07-08 17:02:48.285000",
                },
                "createdAt": "2025-07-08 16:58:14.675000",
                "updatedAt": "2025-07-08 17:02:48.285000",
            }
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            result = get_application_source_resources("test-source-id")

            # Verify API call
            mock_get.assert_called_once_with(
                "https://test.datarobot.com/api/v2/customApplicationSources/test-source-id/",
                headers={
                    "Authorization": "Bearer test-token",
                    "Content-Type": "application/json",
                },
            )

            # Verify result matches actual DataRobot API response structure
            expected_resources = {
                "resourceLabel": "cpu.small",
                "memoryRequest": 268435456,
                "memoryLimit": 536870912,
                "cpuRequest": 0.5,
                "cpuLimit": 1,
                "replicas": 1,
                "sessionAffinity": False,
                "serviceWebRequestsOnRootPath": True,
            }
            assert result == expected_resources

    @patch("utils.datarobot_api_helpers.requests.get")
    def test_get_application_source_resources_no_resources(self, mock_get: Any) -> None:
        """Test API call with no resources returns None"""
        with patch.dict(
            os.environ,
            {
                "DATAROBOT_ENDPOINT": "https://test.datarobot.com",
                "DATAROBOT_API_TOKEN": "test-token",
            },
        ):
            # Mock API response with no resources
            mock_response = Mock()
            mock_response.json.return_value = {"latestVersion": {}}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            result = get_application_source_resources("test-source-id")
            assert result is None

    @patch("utils.datarobot_api_helpers.requests.get")
    def test_get_application_source_resources_latest_version_no_resources(
        self, mock_get: Any
    ) -> None:
        """Test API call where latestVersion exists but has no resources field"""
        with patch.dict(
            os.environ,
            {
                "DATAROBOT_ENDPOINT": "https://test.datarobot.com",
                "DATAROBOT_API_TOKEN": "test-token",
            },
        ):
            # Mock API response with latestVersion but no resources
            mock_response = Mock()
            mock_response.json.return_value = {
                "id": "686d4e1d49b3f25cf01a4c49",
                "name": "Forecasting Assistant Application Source",
                "latestVersion": {
                    "id": "686d4e1e6a752147a67b80aa",
                    "version": 1,
                    "createdAt": "2025-07-08 16:58:14.675000",
                    "updatedAt": "2025-07-08 17:02:48.285000",
                    # No resources field
                },
                "createdAt": "2025-07-08 16:58:14.675000",
                "updatedAt": "2025-07-08 17:02:48.285000",
            }
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            result = get_application_source_resources("test-source-id")
            assert result is None

    @patch("utils.datarobot_api_helpers.requests.get")
    def test_get_application_source_resources_api_error(self, mock_get: Any) -> None:
        """Test API error handling"""
        with patch.dict(
            os.environ,
            {
                "DATAROBOT_ENDPOINT": "https://test.datarobot.com",
                "DATAROBOT_API_TOKEN": "test-token",
            },
        ):
            # Mock API error
            mock_get.side_effect = Exception("API Error")

            with pytest.raises(Exception, match="API Error"):
                get_application_source_resources("test-source-id")

    @patch("utils.datarobot_api_helpers.requests.get")
    def test_get_application_source_resources_endpoint_with_api_v2(
        self, mock_get: Any
    ) -> None:
        """Test API call when endpoint already includes /api/v2"""
        # Mock environment variables with endpoint that includes api/v2
        with patch.dict(
            os.environ,
            {
                "DATAROBOT_ENDPOINT": "https://test.datarobot.com/api/v2",
                "DATAROBOT_API_TOKEN": "test-token",
            },
        ):
            # Mock successful API response based on actual DataRobot API structure
            mock_response = Mock()
            mock_response.json.return_value = {
                "id": "686d4e1d49b3f25cf01a4c49",
                "name": "Forecasting Assistant Application Source",
                "latestVersion": {
                    "id": "686d4e1e6a752147a67b80aa",
                    "version": 1,
                    "resources": {
                        "resourceLabel": "cpu.medium",
                        "memoryRequest": 536870912,
                        "memoryLimit": 1073741824,
                        "cpuRequest": 1.0,
                        "cpuLimit": 2.0,
                        "replicas": 2,
                        "sessionAffinity": True,
                        "serviceWebRequestsOnRootPath": False,
                    },
                    "createdAt": "2025-07-08 16:58:14.675000",
                    "updatedAt": "2025-07-08 17:02:48.285000",
                },
                "createdAt": "2025-07-08 16:58:14.675000",
                "updatedAt": "2025-07-08 17:02:48.285000",
            }
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            result = get_application_source_resources("test-source-id")

            # Verify API call uses correct URL (no double api/v2)
            mock_get.assert_called_once_with(
                "https://test.datarobot.com/api/v2/customApplicationSources/test-source-id/",
                headers={
                    "Authorization": "Bearer test-token",
                    "Content-Type": "application/json",
                },
            )

            # Verify result matches actual DataRobot API response structure
            expected_resources = {
                "resourceLabel": "cpu.medium",
                "memoryRequest": 536870912,
                "memoryLimit": 1073741824,
                "cpuRequest": 1.0,
                "cpuLimit": 2.0,
                "replicas": 2,
                "sessionAffinity": True,
                "serviceWebRequestsOnRootPath": False,
            }
            assert result == expected_resources
