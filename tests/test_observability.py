"""Tests for observability features: structured logging and health check endpoint."""

import json
import logging
from io import StringIO
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from webui import server


class TestStructuredLogging:
    """Tests for structured logging with JSON output."""

    def test_structured_logging_format_json(self):
        """Test that JSONFormatter produces valid JSON logs."""
        from webui.logging_config import JSONFormatter

        # Create a logger with JSONFormatter
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(JSONFormatter())

        test_logger = logging.getLogger("test_json_format")
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.INFO)

        # Log a test message
        test_logger.info("Test JSON message")

        # Verify output is valid JSON
        log_output = log_stream.getvalue().strip()
        log_data = json.loads(log_output)
        assert log_data["level"] == "INFO"
        assert log_data["message"] == "Test JSON message"
        assert "timestamp" in log_data
        assert "logger" in log_data

    def test_structured_logging_includes_context(self):
        """Test that the application's JSONFormatter includes extra context fields."""
        from webui.logging_config import JSONFormatter

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(JSONFormatter())

        test_logger = logging.getLogger("test_context")
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.INFO)

        # logger.info(..., extra={...}) で渡した追加コンテキストが JSON に含まれること
        test_logger.info("Test message", extra={"extra_field": "context_value"})

        log_output = log_stream.getvalue().strip()
        log_data = json.loads(log_output)
        assert log_data["extra_field"] == "context_value"
        assert log_data["message"] == "Test message"


class TestHealthCheckEndpoint:
    """Tests for the /api/health endpoint."""

    @pytest.fixture
    def client(self):
        """Create a test client for the Starlette app."""
        return TestClient(server.app)

    def test_health_endpoint_exists(self, client):
        """Test that the /api/health endpoint returns 200."""
        with (
            patch("webui.server.check_database_connectivity", return_value=True),
            patch("webui.server.check_embedding_service", return_value=True),
        ):
            response = client.get("/api/health")
        assert response.status_code == 200

    def test_health_endpoint_json_response(self, client):
        """Test that /api/health returns JSON with status and services."""
        with (
            patch("webui.server.check_database_connectivity", return_value=True),
            patch("webui.server.check_embedding_service", return_value=True),
        ):
            response = client.get("/api/health")

        data = response.json()
        assert "status" in data
        assert "services" in data
        assert "timestamp" in data
        assert isinstance(data["services"], dict)

    def test_health_endpoint_all_services_ok(self, client):
        """Test health check when all services are healthy."""
        with (
            patch("webui.server.check_database_connectivity", return_value=True),
            patch("webui.server.check_embedding_service", return_value=True),
        ):
            response = client.get("/api/health")

        data = response.json()
        assert response.status_code == 200
        assert data["status"] == "healthy"
        assert data["services"]["db"] == "ok"
        assert data["services"]["embedding"] == "ok"

    def test_health_endpoint_db_unhealthy(self, client):
        """Test health check when database is unavailable."""
        with (
            patch("webui.server.check_database_connectivity", return_value=False),
            patch("webui.server.check_embedding_service", return_value=True),
        ):
            response = client.get("/api/health")

        data = response.json()
        assert response.status_code == 503
        assert data["status"] == "unhealthy"
        assert data["services"]["db"] == "unreachable"
        assert data["services"]["embedding"] == "ok"

    def test_health_endpoint_embedding_unhealthy(self, client):
        """Test health check when embedding service is unavailable."""
        with (
            patch("webui.server.check_database_connectivity", return_value=True),
            patch("webui.server.check_embedding_service", return_value=False),
        ):
            response = client.get("/api/health")

        data = response.json()
        assert response.status_code == 503
        assert data["status"] == "unhealthy"
        assert data["services"]["db"] == "ok"
        assert data["services"]["embedding"] == "unreachable"

    def test_health_endpoint_both_unhealthy(self, client):
        """Test health check when all services are unavailable."""
        with (
            patch("webui.server.check_database_connectivity", return_value=False),
            patch("webui.server.check_embedding_service", return_value=False),
        ):
            response = client.get("/api/health")

        data = response.json()
        assert response.status_code == 503
        assert data["status"] == "unhealthy"
        assert data["services"]["db"] == "unreachable"
        assert data["services"]["embedding"] == "unreachable"

    def test_health_endpoint_has_timestamp(self, client):
        """Test that health endpoint includes a timestamp."""
        with (
            patch("webui.server.check_database_connectivity", return_value=True),
            patch("webui.server.check_embedding_service", return_value=True),
        ):
            response = client.get("/api/health")

        data = response.json()
        assert "timestamp" in data
        # Verify timestamp is a string in ISO format (basic check)
        assert isinstance(data["timestamp"], str)
        assert "T" in data["timestamp"] or "-" in data["timestamp"]
