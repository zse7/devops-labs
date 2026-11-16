"""Проверки таймаута и полного пересоздания локального учебного окружения."""

import argparse
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import time
from uuid import uuid4

from jobs import LAB, ROOT, verify


def timeout_check():
    original = (LAB / "last-job.json").read_bytes()
    record = {
        "job_id": str(uuid4()),
        "status": "submitted",
        "url": "https://quotes.toscrape.com/scroll",
    }
    started = time.monotonic()
    try:
        verify(record, 2)
    except TimeoutError:
        elapsed = time.monotonic() - started
        assert elapsed < 8, elapsed
        assert (LAB / "last-job.json").read_bytes() == original
        print(
            f"Отрицательная проверка timeout: ожидаемая ошибка за {elapsed:.2f} с, last-job не изменён."
        )
    else:
        raise AssertionError("Несуществующее задание ошибочно признано выполненным")


def lifecycle():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = ROOT / "out" / f"lab3-checks-{stamp}"
    report.mkdir(parents=True)
    summary = {"report": str(report), "steps": []}
    calls = {}

    def run(target, *, environment=None, expect_no_changes=False):
        calls[target] = calls.get(target, 0) + 1
        log = report / f"{target}-{calls[target]}.log"
        print(f"Проверка: make {target}; лог {log}", flush=True)
        with log.open("w") as output:
            result = subprocess.run(
                ["make", target],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment,
                timeout=1800,
            )
        if result.returncode:
            raise RuntimeError(
                f"make {target} завершился с кодом {result.returncode}: {log}"
            )
        if expect_no_changes:
            recaps = [
                line
                for line in log.read_text().splitlines()
                if "unreachable=" in line and "failed=" in line
            ]
            assert recaps and all(
                "changed=0 " in line
                and "failed=0 " in line
                and "unreachable=0 " in line
                for line in recaps
            ), recaps
        summary["steps"].append({"target": target, "log": log.name})
        (report / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Пройдено: {target}", flush=True)

    # Сохраняем только доказательства; SSH-ключи и private environment не копируем.
    evidence = report / "previous-evidence"
    evidence.mkdir()
    for path in LAB.iterdir():
        if path.is_file() and path.suffix in (".log", ".json"):
            shutil.copy2(path, evidence / path.name)
    env = dict(os.environ)
    env["LAB_BACKUP_DIR"] = str(report)
    run("backup-results", environment=env)
    assert list((report / "results").glob("*.json")), "Результаты не сохранены"
    before_name = json.loads((LAB / "vm.json").read_text())["name"]
    run("destroy")
    assert not LAB.exists()
    assert not (ROOT / "infra/ansible/inventories/lab").exists()
    run("destroy")
    run("provision")
    assert json.loads((LAB / "vm.json").read_text())["name"] == before_name
    run("image-import")
    run("deploy")
    initial_state = json.loads((LAB / "deploy-state.json").read_text())
    run("provision", expect_no_changes=True)
    run("deploy", expect_no_changes=True)
    assert initial_state == json.loads(
        (LAB / "deploy-state.json").read_text()
    ), "Повтор изменил контейнеры"
    run("ssh-check")
    run("http-check")
    run("run")
    run("verify")
    run("http-isolation-check")
    timeout_check()
    summary["final_job"] = json.loads((LAB / "last-job.json").read_text())
    summary["container_identity_unchanged"] = True
    summary["complete"] = True
    (report / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    shutil.copy2(report / "summary.json", LAB / "lifecycle-test.json")
    archive = LAB / "scraper-image.tar"
    if archive.exists():
        archive.unlink()
    print(
        f"LIFECYCLE PASSED. Доказательства и исходные результаты: {report}", flush=True
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["timeout", "lifecycle"])
    args = parser.parse_args()
    if args.action == "timeout":
        timeout_check()
    else:
        lifecycle()


if __name__ == "__main__":
    main()
