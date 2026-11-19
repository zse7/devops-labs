"""Одно задание Redis и ограниченная по времени проверка его HTTP-результата."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

import yaml

from manage_vm import LAB, ROOT, ssh_command


# Прочитать адрес VM из inventory
def connection():
    host = yaml.safe_load(
        (ROOT / "infra/ansible/inventories/lab/hosts.yml").read_text()
    )["all"]["children"]["lab"]["hosts"]["quotes"]
    name = json.loads((LAB / "vm.json").read_text())["name"]
    return SimpleNamespace(name=name), host["ansible_host"]


# выполняет переданную команду в Ubuntu, подключаясь как админ (Запустить команду через SSH)
def remote(arguments, *, timeout=15):
    args, address = connection()
    result = subprocess.run(
        ssh_command(args, address, "admin", shlex.join(arguments)),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


# Добавить команду Docker Compose и папку проекта
def compose(arguments, *, timeout=15):
    return remote(
        [
            "sudo",
            "-n",
            "docker",
            "compose",
            "--project-directory",
            "/srv/quotes-scraper",
            "--project-name",
            "quotes-scraper",
            *arguments,
        ],
        timeout=timeout,
    )


# Запустить Redis-клиент внутри контейнера и разобрать ответ
def redis(arguments, *, timeout=15):
    return json.loads(
        compose(
            [
                "exec",
                "-T",
                "redis",
                "redis-cli",
                "--json",
                *arguments,
            ],
            timeout=timeout,
        )
    )


def save_record(record):
    temporary = LAB / "last-job.json.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(LAB / "last-job.json")


def enqueue(url, *, fault_phase=None):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL должен содержать HTTP/HTTPS и имя сервера")
    # Проверка до отправки: не кладём работу в неготовое окружение.
    compose(["exec", "-T", "scraper", "node", "src/healthcheck.js"])
    record = {
        "job_id": str(uuid4()),
        "url": url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "prepared",
    }
    save_record(record)
    if fault_phase is not None:
        if fault_phase not in ("after-receive", "after-save"):
            raise ValueError("Неизвестная точка сбоя")
        redis(["SET", f"lab:fault:{record['job_id']}", fault_phase, "EX", "300", "NX"])
    try:
        # Задание отправляется в Redis
        message_id = redis(
            ["XADD", "jobs", "*", "job_id", record["job_id"], "url", url]
        )
        if not isinstance(message_id, str) or not re.fullmatch(r"\d+-\d+", message_id):
            raise RuntimeError(f"Неожиданный ответ XADD: {message_id!r}")
    except Exception:
        # Сообщение могло попасть в Redis до обрыва связи: не отправляем повторно.
        record["status"] = "uncertain"
        save_record(record)
        raise
    record.update(message_id=message_id, status="submitted")
    save_record(record)
    print(f"Отправлено одно задание: {record['job_id']}")
    print(f"Redis ID: {message_id}; URL: {url}")
    return record


def validate_quotes(quotes):
    if not isinstance(quotes, list) or not quotes:
        raise ValueError("JSON должен содержать непустой список цитат")
    for quote in quotes:
        if not isinstance(quote, dict):
            raise ValueError("Цитата должна быть объектом")
        for field in ("text", "author"):
            if not isinstance(quote.get(field), str) or not quote[field].strip():
                raise ValueError(f"У цитаты нет непустого поля {field}")
        tags = quote.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("Поле tags должно быть списком строк")
    return len(quotes)


def verify(record, timeout):
    job_id = record["job_id"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", job_id):
        raise ValueError("Некорректный job_id в локальной записи")
    if record.get("status") not in ("submitted", "verified"):
        raise ValueError(
            "Отправка этого задания не подтверждена; проверь last-job.json"
        )
    _, address = connection()
    result_url = f"http://{address}:8080/results/{job_id}.json"
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_error = "Результат ещё не появился"

    def remaining(limit):
        seconds = min(limit, deadline - time.monotonic())
        if seconds <= 0:
            raise TimeoutError("Время проверки истекло")
        return seconds

    print(f"Ожидание {job_id}, не более {timeout} секунд…", flush=True)
    while time.monotonic() < deadline:
        try:
            with opener.open(result_url, timeout=remaining(5)) as response:
                body = response.read()
            count = validate_quotes(json.loads(body))
            # Сверяем HTTP с конкретным файлом внутри worker, а не со старым JSON.
            metadata = json.loads(
                compose(
                    [
                        "exec",
                        "-T",
                        "scraper",
                        "node",
                        "-e",
                        "const f=require('fs'),c=require('crypto');"
                        "const id=process.argv[1],p='/results/'+id+'.json';"
                        "const b=f.readFileSync(p);"
                        "require('./src/results').validateQuotes(JSON.parse(b));"
                        "if(f.existsSync('/results/.'+id+'.tmp'))throw Error('Temporary file remains');"
                        "console.log(JSON.stringify({sha256:c.createHash('sha256').update(b).digest('hex')}));",
                        job_id,
                    ],
                    timeout=remaining(15),
                )
            )
            if hashlib.sha256(body).hexdigest() != metadata["sha256"]:
                raise ValueError("HTTP-ответ не совпадает с файлом результата")
            pending = redis(["XPENDING", "jobs", "scrapers"], timeout=remaining(15))
            if not isinstance(pending, list) or pending[0] != 0:
                raise ValueError(f"В группе ещё есть pending: {pending!r}")
            record.update(
                status="verified",
                quotes=count,
                sha256=metadata["sha256"],
                result_url=result_url,
            )
            save_record(record)
            (LAB / "last-result.json").write_bytes(body)
            print(
                f"Проверено: {count} цитат; HTTP совпадает с файлом; tmp отсутствует; XPENDING=0."
            )
            print(result_url)
            return record
        except (
            HTTPError,
            URLError,
            OSError,
            ValueError,
            subprocess.SubprocessError,
        ) as error:
            last_error = str(error)
            time.sleep(max(0, min(1, deadline - time.monotonic())))
    raise TimeoutError(
        f"Результат {job_id} не проверен за {timeout} секунд: {last_error}. Логи: make app-logs"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "verify", "logs"])
    args = parser.parse_args()
    if args.action == "run":
        enqueue(os.environ.get("URL", "https://quotes.toscrape.com/scroll"))
    elif args.action == "verify":
        timeout = int(os.environ.get("VERIFY_TIMEOUT", "180"))
        if not 1 <= timeout <= 600:
            parser.error("VERIFY_TIMEOUT должен быть от 1 до 600 секунд")
        verify(json.loads((LAB / "last-job.json").read_text()), timeout)
    else:
        print(compose(["logs", "--no-color", "--tail", "40", "scraper"]))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
