"""Создание лабораторной VM и подготовка проверяемого SSH-доступа."""

import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / ".lab"


def run(command, *, capture=False, timeout=90):
    return subprocess.run(
        command, check=True, text=True, capture_output=capture, timeout=timeout
    )


def write_if_changed(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def client_file_path(executable, path):
    # Windows-клиент Multipass из WSL читает Windows-путь, не /home/... .
    if Path(executable).name.lower().endswith(".exe"):
        return run(["wslpath", "-w", str(path)], capture=True).stdout.strip()
    return str(path)


def ssh_options(args):
    return [
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        f"UserKnownHostsFile={LAB / 'known_hosts'}",
        "-o",
        f"HostKeyAlias={args.name}",
    ]


def ssh_command(args, address, user, command):
    return [
        "ssh",
        *ssh_options(args),
        "-i",
        str(LAB / "id_ed25519"),
        f"{user}@{address}",
        command,
    ]


def address_of(args):
    machines = json.loads(
        run([args.multipass, "list", "--format", "json"], capture=True).stdout
    )["list"]
    machine = next(vm for vm in machines if vm["name"] == args.name)
    if not machine["ipv4"]:
        raise SystemExit("У VM нет IPv4-адреса.")
    return machine["ipv4"][0]


def up(args):
    mp = [args.multipass]
    machines = json.loads(run(mp + ["list", "--format", "json"], capture=True).stdout)[
        "list"
    ]
    existing = next((vm for vm in machines if vm["name"] == args.name), None)
    marker = LAB / "vm.json"
    if marker.exists():
        if json.loads(marker.read_text())["name"] != args.name:
            raise SystemExit(
                "В .lab уже зарегистрирована другая VM; имя не менять автоматически."
            )
    elif existing:
        raise SystemExit(
            "VM с таким именем уже есть, но она не зарегистрирована в .lab. Не изменяю её."
        )

    if not existing:
        # Маркер сохраняется до launch, чтобы после таймаута не потерять созданную VM.
        write_if_changed(marker, json.dumps({"name": args.name}) + "\n")
        run(
            mp
            + [
                "launch",
                "24.04",
                "--name",
                args.name,
                "--cpus",
                "2",
                "--memory",
                "4G",
                "--disk",
                "20G",
                "--cloud-init",
                client_file_path(args.multipass, LAB / "cloud-init.yaml"),
                "--timeout",
                "900",
            ],
            timeout=960,
        )
    elif existing["state"] == "Stopped":
        run(mp + ["start", args.name], timeout=180)
    elif existing["state"] != "Running":
        raise SystemExit(
            f"VM имеет состояние {existing['state']}; автоматические изменения остановлены."
        )

    known_hosts = LAB / "known_hosts"
    if not known_hosts.exists():
        # Только первое установление доверия идёт через локальный Multipass.
        # После hardening все гостевые чтения выполняются как admin по SSH.
        for attempt in range(12):
            probe = subprocess.run(
                mp + ["exec", args.name, "--", "true"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if probe.returncode == 0:
                break
            if probe.returncode != 255 or attempt == 11:
                raise SystemExit(
                    f"VM недоступна через Multipass: {probe.stderr.strip()}"
                )
            time.sleep(3)
        run(
            mp
            + [
                "exec",
                args.name,
                "--",
                "sudo",
                "timeout",
                "600",
                "cloud-init",
                "status",
                "--wait",
            ],
            timeout=660,
        )
        public_host_key = run(
            mp + ["exec", args.name, "--", "cat", "/etc/ssh/ssh_host_ed25519_key.pub"],
            capture=True,
        ).stdout.split()
        if len(public_host_key) < 2 or public_host_key[0] != "ssh-ed25519":
            raise SystemExit("Не получен ожидаемый публичный host key VM.")
        write_if_changed(known_hosts, f"{args.name} {' '.join(public_host_key[:2])}\n")

    address = address_of(args)
    user = None
    for candidate in ("admin", "lab-bootstrap"):
        attempt = subprocess.run(
            ssh_command(args, address, candidate, "sudo -n true"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if attempt.returncode == 0:
            user = candidate
            break
    if user is None:
        raise SystemExit(
            "Не удалось проверить вход по ключу и sudo. Проверь доступ к VM по SSH."
        )

    run(
        ssh_command(args, address, user, "sudo timeout 600 cloud-init status --wait"),
        timeout=660,
    )
    run(ssh_command(args, address, user, "sudo cloud-init schema --system"))
    release = run(
        ssh_command(args, address, user, "cat /etc/os-release"), capture=True
    ).stdout
    if "ID=ubuntu\n" not in release or 'VERSION_ID="24.04"' not in release:
        raise SystemExit("VM не соответствует Ubuntu 24.04.")

    # Аргументы SSH сохраняются с кавычками для путей с пробелами.
    inventory = {
        "all": {
            "children": {
                "lab": {
                    "hosts": {
                        "quotes": {
                            "ansible_host": address,
                            "ansible_user": user,
                            "ansible_python_interpreter": "/usr/bin/python3",
                            "ansible_ssh_private_key_file": str(LAB / "id_ed25519"),
                            "ansible_ssh_common_args": shlex.join(ssh_options(args)),
                        }
                    }
                }
            }
        }
    }
    write_if_changed(
        ROOT / "infra/ansible/inventories/lab/hosts.yml",
        yaml.safe_dump(inventory, sort_keys=False),
    )
    print(f"Ubuntu 24.04 готова: {args.name}, IP {address}, SSH-пользователь {user}.")
    print("Host key проверен, вход по ключу и sudo работают. Inventory сформирован.")


def verify_ssh(args):
    address = address_of(args)
    run(ssh_command(args, address, "admin", "sudo -n true"))
    for user in ("lab-bootstrap", "ubuntu", "root"):
        result = subprocess.run(
            ssh_command(args, address, user, "true"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 255 or "Permission denied" not in result.stderr:
            raise SystemExit(
                f"Ожидался отказ SSH для {user}, получен код {result.returncode}: {result.stderr}"
            )
        print(f"SSH-вход {user}: запрещён (проверено новым соединением).")
    print("SSH-вход admin и sudo: работают.")


def destroy(args):
    if LAB.is_symlink():
        raise SystemExit("Не удаляю .lab: это символическая ссылка.")
    marker = LAB / "vm.json"
    machines = json.loads(
        run([args.multipass, "list", "--format", "json"], capture=True).stdout
    )["list"]
    exists = any(vm["name"] == args.name for vm in machines)
    if marker.exists() and json.loads(marker.read_text())["name"] != args.name:
        raise SystemExit(
            "Имя VM не совпадает с локальным маркером: удаление запрещено."
        )
    if exists:
        if not marker.exists():
            raise SystemExit(
                "VM существует без нашего маркера; не удаляю чужое окружение."
            )
        run([args.multipass, "delete", "--purge", args.name], timeout=180)
    if LAB.exists():
        shutil.rmtree(LAB)
    inventory = ROOT / "infra/ansible/inventories/lab"
    if inventory.is_symlink():
        raise SystemExit("Не удаляю inventory: каталог является ссылкой.")
    if inventory.exists():
        shutil.rmtree(inventory)
    print(
        "Учебная VM и её локальные служебные файлы удалены; повторный destroy безопасен."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--multipass", default="multipass")
    parser.add_argument("--name", default="devops-lab3-quotes")
    parser.add_argument("action", choices=["up", "verify-ssh", "destroy"])
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", args.name):
        parser.error("Недопустимое имя виртуальной машины.")
    if args.action == "up":
        up(args)
    elif args.action == "verify-ssh":
        verify_ssh(args)
    else:
        destroy(args)


if __name__ == "__main__":
    main()
