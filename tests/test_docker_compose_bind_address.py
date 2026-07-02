"""Test docker-compose.yml bind address configuration (Issue #13).

Validates that development Docker services bind only to 127.0.0.1 (localhost),
not to 0.0.0.0 (all interfaces), for security reasons.
"""

import yaml
from pathlib import Path


def load_docker_compose():
    """Load docker-compose.yml from docker/ directory."""
    compose_path = Path(__file__).parent.parent / "docker" / "docker-compose.yml"
    with open(compose_path) as f:
        return yaml.safe_load(f)


def test_docker_compose_yaml_is_valid():
    """Test that docker-compose.yml is valid YAML."""
    compose = load_docker_compose()
    assert compose is not None
    assert "services" in compose


def test_db_service_binds_to_localhost():
    """Test that PostgreSQL db service binds only to 127.0.0.1."""
    compose = load_docker_compose()
    db_service = compose["services"]["db"]

    assert "ports" in db_service
    ports = db_service["ports"]

    # Should have at least one port mapping
    assert len(ports) > 0

    # All port mappings should bind to 127.0.0.1 (not 0.0.0.0)
    for port_mapping in ports:
        assert not port_mapping.startswith("5432:"), \
            "DB port 5432 should bind to 127.0.0.1, not default interface"
        assert port_mapping.startswith("127.0.0.1:5432:") or \
               port_mapping.startswith("localhost:5432:"), \
            f"Expected db port to bind to 127.0.0.1 or localhost, got: {port_mapping}"


def test_ollama_service_binds_to_localhost():
    """Test that Ollama service binds only to 127.0.0.1."""
    compose = load_docker_compose()
    ollama_service = compose["services"]["ollama"]

    assert "ports" in ollama_service
    ports = ollama_service["ports"]

    # Should have at least one port mapping
    assert len(ports) > 0

    # All port mappings should bind to 127.0.0.1 (not 0.0.0.0)
    for port_mapping in ports:
        assert not port_mapping.startswith("11434:"), \
            "Ollama port 11434 should bind to 127.0.0.1, not default interface"
        assert port_mapping.startswith("127.0.0.1:11434:") or \
               port_mapping.startswith("localhost:11434:"), \
            f"Expected ollama port to bind to 127.0.0.1 or localhost, got: {port_mapping}"


def test_minio_service_binds_to_localhost():
    """Test that MinIO service binds only to 127.0.0.1."""
    compose = load_docker_compose()
    minio_service = compose["services"]["minio"]

    assert "ports" in minio_service
    ports = minio_service["ports"]

    # Should have at least two port mappings (9000, 9001)
    assert len(ports) >= 2

    # All port mappings should bind to 127.0.0.1 (not 0.0.0.0)
    for port_mapping in ports:
        assert not port_mapping.startswith("9000:") and \
               not port_mapping.startswith("9001:"), \
            f"MinIO ports should bind to 127.0.0.1, not default interface: {port_mapping}"

        assert port_mapping.startswith("127.0.0.1:9000:") or \
               port_mapping.startswith("127.0.0.1:9001:") or \
               port_mapping.startswith("localhost:9000:") or \
               port_mapping.startswith("localhost:9001:"), \
            f"Expected minio port to bind to 127.0.0.1 or localhost, got: {port_mapping}"


def test_localstack_service_binds_to_localhost():
    """Test that LocalStack service binds only to 127.0.0.1."""
    compose = load_docker_compose()
    localstack_service = compose["services"]["localstack"]

    assert "ports" in localstack_service
    ports = localstack_service["ports"]

    # Should have at least one port mapping
    assert len(ports) > 0

    # All port mappings should bind to 127.0.0.1 (not 0.0.0.0)
    for port_mapping in ports:
        assert not port_mapping.startswith("4566:"), \
            "LocalStack port 4566 should bind to 127.0.0.1, not default interface"
        assert port_mapping.startswith("127.0.0.1:4566:") or \
               port_mapping.startswith("localhost:4566:"), \
            f"Expected localstack port to bind to 127.0.0.1 or localhost, got: {port_mapping}"


def test_no_wildcard_bindings():
    """Test that no services bind to 0.0.0.0."""
    compose = load_docker_compose()
    services = compose["services"]

    # Services that should have ports specified
    services_with_ports = ["db", "ollama", "minio", "localstack"]

    for service_name in services_with_ports:
        if service_name not in services:
            continue

        service = services[service_name]
        if "ports" not in service:
            continue

        ports = service["ports"]
        for port_mapping in ports:
            assert not port_mapping.startswith("0.0.0.0:"), \
                f"Service '{service_name}' binds to 0.0.0.0 - security issue! Got: {port_mapping}"
