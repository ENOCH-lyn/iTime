from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
DOCKERFILE = SERVER / "Dockerfile.recovery-postgres"


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
