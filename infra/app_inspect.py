"""Чтение состояния только проекта quotes-scraper, без вывода environment."""

import json
import subprocess

ids = subprocess.check_output(
    [
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        "label=com.docker.compose.project=quotes-scraper",
    ],
    text=True,
).split()
if not ids:
    raise SystemExit("Контейнеры проекта quotes-scraper не найдены")
containers = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
network_names = sorted(
    {
        name
        for container in containers
        for name in container["NetworkSettings"]["Networks"]
    }
)
networks = json.loads(
    subprocess.check_output(["docker", "network", "inspect", *network_names], text=True)
)
internal = {network["Name"]: network["Internal"] for network in networks}
summary = {}
for container in containers:
    service = container["Config"]["Labels"]["com.docker.compose.service"]
    if service in summary:
        raise SystemExit(f"Для сервиса {service} обнаружено несколько контейнеров")
    summary[service] = {
        "id": container["Id"],
        "image": container["Image"],
        "started_at": container["State"]["StartedAt"],
        "state": container["State"]["Status"],
        "health": container["State"].get("Health", {}).get("Status"),
        "ports": container["HostConfig"].get("PortBindings") or {},
        "mounts": [
            {k: mount[k] for k in ("Type", "Source", "Destination", "RW")}
            for mount in container["Mounts"]
        ],
        "user": container["Config"]["User"],
        "networks": {
            name: internal[name] for name in container["NetworkSettings"]["Networks"]
        },
    }
print(json.dumps(summary, sort_keys=True))
