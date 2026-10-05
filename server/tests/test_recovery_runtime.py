import json
import os
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
DOCKERFILE = SERVER / "Dockerfile.recovery-postgres"
COMPOSE = ROOT / "compose.recovery.yaml"
TEMP_DIR = str(Path("/", "tmp"))


def recovery_environment(tmp_path: Path) -> dict[str, str]:
    source_config = tmp_path / "source.conf"
    restore_config = tmp_path / "restore.conf"
    source_config.write_text("[global]\nrepo1-path=/var/lib/pgbackrest\n")
    restore_config.write_text("[global]\nrepo1-path=/var/lib/pgbackrest\n")
    key = Fernet.generate_key().decode()
    return {
        **os.environ,
        "ITIME_RECOVERY_RUN_ID": "abc12345",
        "ITIME_RECOVERY_SOURCE_DATABASE": "itime_backup_abc12345",
        "ITIME_RECOVERY_RESTORE_DATABASE": "itime_restore_abc12345",
        "ITIME_RECOVERY_POSTGRES_PASSWORD": "synthetic-only",
        "ITIME_RECOVERY_SOURCE_CONFIG": str(source_config),
        "ITIME_RECOVERY_RESTORE_CONFIG": str(restore_config),
        "ITIME_RECOVERY_ENCRYPTION_KEY": key,
        "ITIME_RECOVERY_AI_ENCRYPTION_KEY": key,
        "ITIME_RECOVERY_CALENDAR_KEY": key,
        "ITIME_RECOVERY_EXPORT_KEY": key,
        "ITIME_RECOVERY_SOURCE_DB_PORT": "15432",
        "ITIME_RECOVERY_RESTORE_DB_PORT": "15433",
        "ITIME_RECOVERY_API_PORT": "18000",
    }


def load_recovery_compose_config(tmp_path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE),
            "--profile",
            "restore",
            "--profile",
            "app",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=recovery_environment(tmp_path),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_recovery_image_pins_postgres_and_safe_pgbackrest():
    content = DOCKERFILE.read_text()

    assert content.count(
        "postgres:18-alpine@"
        "sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873"
    ) == 2
    assert "ARG PGBACKREST_VERSION=2.59.3" in content
    assert (
        "ARG PGBACKREST_SHA256="
        "14037901db002e5536a948bf9f0fc0ff6cde31f4e675d3e9b46f129071bf2e5f"
    ) in content
    assert "sha256sum -c -" in content
    assert "USER postgres" in content


def test_recovery_image_keeps_build_dependencies_out_of_final_stage():
    content = DOCKERFILE.read_text()
    _, final_stage = content.rsplit(
        "FROM postgres:18-alpine@"
        "sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873",
        maxsplit=1,
    )

    assert "build-base" not in final_stage
    assert "meson" not in final_stage
    assert "ninja" not in final_stage
    assert (
        "COPY --from=build /usr/local/bin/pgbackrest "
        "/usr/local/bin/pgbackrest"
    ) in final_stage
    assert "/var/lib/pgbackrest" in final_stage
    assert "chown -R postgres:postgres" in final_stage


def test_recovery_compose_separates_source_restore_repository_and_profiles(
    tmp_path: Path,
):
    config = load_recovery_compose_config(tmp_path)
    services = config["services"]
    assert isinstance(services, dict)
    assert set(services) == {
        "source-postgres",
        "restore-postgres",
        "restore-api",
    }

    source = services["source-postgres"]
    restore = services["restore-postgres"]
    api = services["restore-api"]
    assert source["image"] == "itime-recovery-postgres:local"
    assert restore["image"] == "itime-recovery-postgres:local"
    assert {
        "archive_mode=on",
        "archive_timeout=60s",
        "wal_level=replica",
        "archive_command=pgbackrest --stanza=itime archive-push %p",
    } <= set(source["command"])
    assert "restore" in restore["profiles"]
    assert set(api["profiles"]) == {"app"}
    assert api["image"] == "itime-performance:local"
    assert api["depends_on"]["restore-postgres"]["condition"] == "service_healthy"
    assert (
        "restore-postgres:5432/itime_restore_abc12345"
        in api["environment"]["ITIME_DATABASE_URL"]
    )
    assert api["ports"] == [
        {
            "mode": "ingress",
            "target": 8000,
            "published": "18000",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]

    volumes = config["volumes"]
    assert isinstance(volumes, dict)
    assert set(volumes) == {
        "source_data",
        "restore_data",
        "repository",
        "spool",
        "restore_spool",
    }
    volume_names = {volume["name"] for volume in volumes.values()}
    assert len(volume_names) == 5
    assert volume_names == {
        "itime-recovery-abc12345_source_data",
        "itime-recovery-abc12345_restore_data",
        "itime-recovery-abc12345_repository",
        "itime-recovery-abc12345_spool",
        "itime-recovery-abc12345_restore_spool",
    }

    source_mounts = {mount["target"]: mount for mount in source["volumes"]}
    restore_mounts = {mount["target"]: mount for mount in restore["volumes"]}
    assert source_mounts["/var/lib/postgresql"]["source"] == "source_data"
    assert restore_mounts["/var/lib/postgresql"]["source"] == "restore_data"
    assert source_mounts["/var/lib/pgbackrest"]["source"] == "repository"
    assert restore_mounts["/var/lib/pgbackrest"] == {
        "type": "volume",
        "source": "repository",
        "target": "/var/lib/pgbackrest",
        "read_only": True,
        "volume": {},
    }
    assert source_mounts["/var/spool/pgbackrest"]["source"] == "spool"
    assert restore_mounts["/var/spool/pgbackrest"]["source"] == "restore_spool"
    for mounts in (source_mounts, restore_mounts):
        assert mounts["/etc/pgbackrest/pgbackrest.conf"]["type"] == "bind"
        assert mounts["/etc/pgbackrest/pgbackrest.conf"]["read_only"] is True
    assert (
        source_mounts["/etc/pgbackrest/pgbackrest.conf"]["source"]
        != restore_mounts["/etc/pgbackrest/pgbackrest.conf"]["source"]
    )

    networks = config["networks"]
    assert networks["default"]["name"] == "itime-recovery-abc12345_default"
    assert networks["default"]["internal"] is True
    assert (
        networks["host_access"]["name"]
        == "itime-recovery-abc12345_host_access"
    )
    assert networks["host_access"].get("internal", False) is False
    for service in services.values():
        assert set(service["networks"]) == {"default", "host_access"}


def test_recovery_compose_stays_inside_runtime_security_budget(tmp_path: Path):
    config = load_recovery_compose_config(tmp_path)
    services = config["services"]
    assert isinstance(services, dict)

    assert sum(int(service["mem_limit"]) for service in services.values()) <= 4 * 1024**3
    for name, service in services.items():
        assert service["restart"] == "no", name
        assert service["pids_limit"] <= 128, name
        assert service["read_only"] is True, name
        assert not service.get("privileged", False), name
        assert "no-new-privileges:true" in service["security_opt"], name

    for name in ("source-postgres", "restore-postgres", "restore-api"):
        service = services[name]
        assert service["ports"][0]["host_ip"] == "127.0.0.1"
        assert int(service["ports"][0]["published"]) > 0

    assert services["restore-api"]["read_only"] is True
    assert f"{TEMP_DIR}:size=64m,mode=1777" in services["restore-api"]["tmpfs"]
