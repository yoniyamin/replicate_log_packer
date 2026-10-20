import time
from dataclasses import replace

from fastapi.testclient import TestClient

from logpacker.config import get_settings
from logpacker.web.app import create_app


def make_client(tmp_path):
    settings = replace(get_settings(), data_dir=tmp_path / "data")
    return TestClient(create_app(settings))


def wait_ready(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("ready", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_full_flow(tmp_path, sample_log):
    client = make_client(tmp_path)
    job = client.post("/api/jobs", json={"filename": sample_log.name, "size": sample_log.stat().st_size,
                                         "sanitize": {"custom_terms": ["SALES_TO_SNOW"]}}).json()
    up = client.put(f"/api/jobs/{job['id']}/content", content=sample_log.read_bytes())
    assert up.status_code == 200
    assert wait_ready(client, job["id"])["state"] == "ready"

    report = client.get(f"/api/jobs/{job['id']}/report").json()
    assert report["stats"]["lines"] > 1000
    privacy = client.get(f"/api/jobs/{job['id']}/privacy").json()
    assert any(m["original"] == "SALES_TO_SNOW" for m in privacy["mapping"])

    packed = client.post(f"/api/jobs/{job['id']}/pack", json={"focus": "errors", "question": "Why does it fail?"}).json()
    assert packed["prompt"]["text"].count("Why does it fail?") == 1
    assert "SALES_TO_SNOW" not in packed["prompt"]["text"]
    assert packed["attachment"]["preview"]
    for kind in ("package", "prompt", "attachment", "findings"):
        assert client.get(f"/api/jobs/{job['id']}/download/{kind}").status_code == 200

    assert [j["id"] for j in client.get("/api/jobs").json()] == [job["id"]]
    client.delete(f"/api/jobs/{job['id']}")
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_bad_pack_option_and_not_ready(tmp_path):
    client = make_client(tmp_path)
    job = client.post("/api/jobs", json={"filename": "x.log", "size": 3}).json()
    assert client.post(f"/api/jobs/{job['id']}/pack", json={}).status_code == 409
    assert client.put(f"/api/jobs/{job['id']}/content", content=b"").status_code == 400
