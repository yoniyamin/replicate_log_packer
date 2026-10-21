import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

from fastapi.testclient import TestClient

from logpacker.config import get_settings
from logpacker.web.app import create_app


@contextmanager
def server(tmp_path, workers=4):
    settings = replace(get_settings(), data_dir=tmp_path / "data", workers=workers)
    app = create_app(settings)
    with TestClient(app) as client:  # `with` runs the lifespan (job store + worker pool)
        yield app, client


def session(app):
    """A second browser: same app, separate cookie jar."""
    return TestClient(app)


def wait_ready(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("ready", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def upload(client, path, fingerprint=None, **sanitize):
    body = {"filename": path.name, "size": path.stat().st_size, "sanitize": sanitize}
    if fingerprint:
        body["fingerprint"] = fingerprint
    job = client.post("/api/jobs", json=body).json()
    if job.get("cached"):
        return job
    return client.put(f"/api/jobs/{job['id']}/content", content=path.read_bytes()).json()


def test_full_flow(tmp_path, sample_log):
    with server(tmp_path) as (_, client):
        job = upload(client, sample_log, custom_terms=["SALES_TO_SNOW"])
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


def test_repeat_upload_reuses_analysis(tmp_path, sample_log):
    with server(tmp_path) as (app, client):
        first = upload(client, sample_log, fingerprint="abc")
        wait_ready(client, first["id"])

        again = upload(client, sample_log, fingerprint="abc")
        assert again["cached"] and again["id"] == first["id"]

        renamed = sample_log.with_name("copy.log")
        renamed.write_bytes(sample_log.read_bytes())
        by_content = upload(client, renamed, fingerprint="other")
        assert by_content["cached"] and by_content["id"] == first["id"]

        different_options = upload(client, sample_log, fingerprint="abc", mask_objects=True)
        assert not different_options.get("cached")


def test_sessions_are_isolated(tmp_path, sample_log):
    with server(tmp_path) as (app, alice):
        bob = session(app)
        job = upload(alice, sample_log, fingerprint="f1")
        wait_ready(alice, job["id"])
        assert bob.get(f"/api/jobs/{job['id']}/privacy").status_code == 404
        assert bob.get("/api/jobs").json() == []

        shared = upload(bob, sample_log, fingerprint="f1")  # bob uploads the same file
        assert shared["cached"] and shared["id"] == job["id"]
        bob.delete(f"/api/jobs/{job['id']}")
        assert alice.get(f"/api/jobs/{job['id']}").status_code == 200  # alice still has it


def test_ten_sessions_in_parallel(tmp_path, sample_log):
    with server(tmp_path, workers=10) as (app, _):
        clients = [session(app) for _ in range(10)]
        paths = []
        for i in range(10):
            p = tmp_path / f"log_{i}.log"
            p.write_text(sample_log.read_text(encoding="utf-8") + f"# variant {i}\n", encoding="utf-8")
            paths.append(p)

        def one(i):
            c = clients[i]
            job = upload(c, paths[i])
            done = wait_ready(c, job["id"], timeout=120)
            assert done["state"] == "ready", done["error"]
            packed = c.post(f"/api/jobs/{job['id']}/pack", json={}).json()
            return job["id"], packed["attachment"]["lines_kept"]

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(one, range(10)))
        assert len({job_id for job_id, _ in results}) == 10
        assert all(kept > 0 for _, kept in results)


def test_retention_sweeps_unused_jobs(tmp_path, sample_log):
    with server(tmp_path) as (app, client):
        job = upload(client, sample_log)
        wait_ready(client, job["id"])
        store = app.state.jobs
        assert store.sweep(now=time.time() + 6 * 24 * 3600) == []
        assert store.sweep(now=time.time() + 8 * 24 * 3600) == [job["id"]]
        assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_bad_requests(tmp_path):
    with server(tmp_path) as (_, client):
        job = client.post("/api/jobs", json={"filename": "x.log", "size": 3}).json()
        assert client.post(f"/api/jobs/{job['id']}/pack", json={}).status_code == 409
        assert client.put(f"/api/jobs/{job['id']}/content", content=b"").status_code == 400
