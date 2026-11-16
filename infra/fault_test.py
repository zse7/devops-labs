"""Проверки настоящего worker на VM: SIGKILL, pending, дубликат и AOF."""

import json
import os
import subprocess
import time

from jobs import LAB, compose, enqueue, redis, remote, verify


def make(target):
    subprocess.run(["make", target], check=True, timeout=240)


def wait_for(check, label, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.5)
    raise TimeoutError(label)


def metadata(job_id):
    return json.loads(
        remote(
            [
                "sudo",
                "-n",
                "python3",
                "-c",
                "import pathlib,hashlib,json,sys;"
                "p=pathlib.Path('/srv/quotes-scraper/results')/(sys.argv[1]+'.json');"
                "print(json.dumps({'exists':p.exists(), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None,"
                "'mtime_ns':p.stat().st_mtime_ns if p.exists() else None,"
                "'count':len(list(p.parent.glob(sys.argv[1]+'*.json')))}))",
                job_id,
            ]
        )
    )


def group_info():
    for group in redis(["XINFO", "GROUPS", "jobs"]):
        if isinstance(group, list):
            group = dict(zip(group[::2], group[1::2]))
        if group["name"] == "scrapers":
            return group
    raise RuntimeError("Группа scrapers отсутствует")


def delivered(message_id):
    last = group_info()["last-delivered-id"]
    return tuple(map(int, last.split("-"))) >= tuple(map(int, message_id.split("-")))


def main():
    if redis(["XPENDING", "jobs", "scrapers"])[0] != 0:
        raise RuntimeError(
            "Перед fault-test нужно завершить существующие pending-задания"
        )
    evidence = []
    active_keys = []
    mode_requested = False
    try:
        mode_requested = True
        make("fault-mode-on")
        for phase in ("after-receive", "after-save"):
            record = enqueue(
                os.environ.get("URL", "https://quotes.toscrape.com/scroll"),
                fault_phase=phase,
            )
            active_keys.append(f"lab:fault:{record['job_id']}")
            container = compose(["ps", "--all", "--quiet", "scraper"]).strip()

            def exited():
                state = json.loads(
                    remote(
                        [
                            "sudo",
                            "-n",
                            "docker",
                            "inspect",
                            container,
                            "--format",
                            "{{json .State}}",
                        ]
                    )
                )
                return state if not state["Running"] else None

            state = wait_for(
                exited, "Worker не завершился в заданной точке", timeout=150
            )
            assert state["ExitCode"] == 137, state
            pending = redis(
                [
                    "XPENDING",
                    "jobs",
                    "scrapers",
                    record["message_id"],
                    record["message_id"],
                    "1",
                ]
            )
            assert len(pending) == 1 and pending[0][0] == record["message_id"], pending
            before = metadata(record["job_id"])
            assert before["exists"] == (phase == "after-save"), before
            print(
                f"Доказано {phase}: exit=137, конкретное сообщение pending, file_exists={before['exists']}",
                flush=True,
            )
            make("fault-mode-on")
            verified = verify(record, 180)
            after = metadata(record["job_id"])
            if before["exists"]:
                assert before == after, "Восстановление перезаписало готовый JSON"
            duplicate_id = redis(
                ["XADD", "jobs", "*", "job_id", record["job_id"], "url", record["url"]]
            )
            wait_for(lambda: delivered(duplicate_id), "Дубликат не доставлен")
            verify(record, 60)
            assert metadata(record["job_id"]) == after, "Дубликат изменил файл / mtime"
            assert after["count"] == 1, after
            evidence.append(
                {
                    "phase": phase,
                    "job_id": record["job_id"],
                    "message_id": record["message_id"],
                    "exit_code": state["ExitCode"],
                    "pending_before_restart": pending,
                    "sha256": verified["sha256"],
                    "duplicate_id": duplicate_id,
                    "unchanged": True,
                }
            )
            print(
                f"Дубликат {record['job_id']}: один JSON, содержимое и mtime неизменны.",
                flush=True,
            )
    finally:
        try:
            for key in active_keys:
                redis(["DEL", key])
        finally:
            if mode_requested:
                make("fault-mode-off")

    # После восстановления штатной restart policy проверяем реальное AOF.
    count_before = redis(["XLEN", "jobs"])
    group_before = group_info()["last-delivered-id"]
    make("redis-recreate")
    assert redis(["XLEN", "jobs"]) == count_before
    assert group_info()["last-delivered-id"] == group_before

    def worker_ready():
        try:
            return (
                compose(["exec", "-T", "scraper", "node", "src/healthcheck.js"]) == ""
            )
        except subprocess.CalledProcessError:
            return False

    wait_for(worker_ready, "Worker не восстановил Redis-соединение")
    (LAB / "fault-test.json").write_text(
        json.dumps({"scenarios": evidence, "redis_persistence": True}, indent=2) + "\n"
    )
    print(
        "FAULT-TEST PASSED: оба сбоя, дубликаты и сохранность Redis после пересоздания проверены."
    )


if __name__ == "__main__":
    main()
