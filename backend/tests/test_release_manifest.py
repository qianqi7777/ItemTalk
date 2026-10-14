"""发布记录拒绝混用/注入，实际Compose解析摘要及旧标签两种启动配置。"""

import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = runpy.run_path(str(ROOT / "infra" / "release_manifest.py"))
release_record = SCRIPT["release_record"]
SHA = "a" * 40
DIGESTS = {
    "api": "sha256:" + "b" * 64,
    "db": "sha256:" + "c" * 64,
    "frontend": "sha256:" + "d" * 64,
}


def test_release_record_binds_three_images_to_verified_commit():
    record = release_record(SHA, "123", "0009_personal_manuals", DIGESTS)
    assert record["commitSha"] == SHA and record["ciRunId"] == 123
    assert record["compatibility"] == "requires-maintenance"
    assert (
        record["images"]["api"]["reference"] == f"ghcr.io/qianqi7777/itemtalk-api@{DIGESTS['api']}"
    )
    assert record["images"]["db"]["tag"].endswith(f":sha-{SHA}")


@pytest.mark.parametrize(
    ("sha", "ci_run", "revision", "digests"),
    [
        ("main", "123", "0009", DIGESTS),
        (SHA + "\nDB_PASSWORD=secret", "123", "0009", DIGESTS),
        (SHA, "0", "0009", DIGESTS),
        (SHA, "123", "0009\nAPI_IMAGE=other", DIGESTS),
        (SHA, "123", "0009", {"api": DIGESTS["api"]}),
        (SHA, "123", "0009", {**DIGESTS, "db": "latest"}),
        (SHA, "123", "0009", {**DIGESTS, "other": DIGESTS["api"]}),
    ],
)
def test_invalid_or_incomplete_release_cannot_generate_environment(sha, ci_run, revision, digests):
    with pytest.raises(ValueError):
        release_record(sha, ci_run, revision, digests)


def test_operator_output_is_exclusive_and_never_copies_other_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_SHA", SHA)
    monkeypatch.setenv("CI_RUN_ID", "123")
    monkeypatch.setenv("SCHEMA_REVISION", "0009_personal_manuals")
    monkeypatch.setenv("DB_PASSWORD", "private-test-sentinel")
    for name, digest in DIGESTS.items():
        monkeypatch.setenv(f"{name.upper()}_DIGEST", digest)
    output = tmp_path / "release"
    monkeypatch.setattr("sys.argv", ["release_manifest", "--output-directory", str(output)])
    SCRIPT["main"]()
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    assert set(before) == {"release.json", "image-overrides.env"}
    assert all(b"private-test-sentinel" not in content for content in before.values())
    assert json.loads(before["release.json"])["images"]["db"]["reference"].endswith(DIGESTS["db"])
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    with pytest.raises(SystemExit) as error:
        SCRIPT["main"]()
    assert error.value.code == 1 and before == {
        path.name: path.read_bytes() for path in output.iterdir()
    }


@pytest.mark.parametrize("by_digest", [False, True])
def test_real_compose_uses_matching_api_worker_migration_images(tmp_path, by_digest):
    standalone = ROOT / ".cache" / "docker-compose.exe"
    if standalone.is_file():
        command = [str(standalone)]
    elif shutil.which("docker"):
        command = ["docker", "compose"]
    else:
        pytest.skip("Docker Compose CLI is required to verify image interpolation")
    record = release_record(SHA, "123", "0009_personal_manuals", DIGESTS)
    # 只解析配置，不启动容器；显式假配置防止读取本机真实.env。
    values = {
        "DB_ADMIN_PASSWORD": "test-only",
        "DB_MIGRATION_PASSWORD": "test-only",
        "DB_WORKER_PASSWORD": "test-only",
        "DB_PASSWORD": "test-only",
        "JWT_SECRET": "test-only",
        "MODEL_API_KEY": "test-only",
        "SEARCH_API_KEY": "test-only",
        "IMAGE_TAG": f"sha-{SHA}",
        "RELEASE_SHA": SHA,
    }
    if by_digest:
        values.update(
            {
                f"{name.upper()}_IMAGE": image["reference"]
                for name, image in record["images"].items()
            }
        )
    environment = tmp_path / "test.env"
    environment.write_text("\n".join(f"{name}={value}" for name, value in values.items()))
    child_environment = {
        name: value
        for name, value in os.environ.items()
        if name not in values and name not in {"API_IMAGE", "DB_IMAGE", "FRONTEND_IMAGE"}
    }
    result = subprocess.run(  # noqa: S603
        [
            *command,
            "--env-file",
            str(environment),
            "--profile",
            "maintenance",
            "-f",
            str(ROOT / "docker-compose.yml"),
            "config",
            "--format",
            "json",
        ],
        env=child_environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    services = json.loads(result.stdout)["services"]
    field = "reference" if by_digest else "tag"
    for service in ("api", "worker", "migration"):
        assert services[service]["image"] == record["images"]["api"][field]
        assert services[service]["environment"]["BUILD_VERSION"] == SHA
    assert services["db"]["image"] == record["images"]["db"][field]
    assert services["caddy"]["image"] == record["images"]["frontend"][field]
