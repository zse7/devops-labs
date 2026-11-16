"""Подготовка локального SSH-ключа и cloud-init без создания VM."""

from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parent.parent
    lab = root / ".lab"
    lab.mkdir(mode=0o700, exist_ok=True)
    lab.chmod(0o700)
    private_key = lab / "id_ed25519"
    public_key = lab / "id_ed25519.pub"

    if not private_key.exists():
        if public_key.exists():
            raise SystemExit(
                "Публичный ключ уже есть, приватного нет: проверь .lab вручную."
            )
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "devops-lab3",
                "-f",
                str(private_key),
            ],
            check=True,
        )

    private_key.chmod(0o600)
    # Проверяем пару без вывода ключей и не заменяем существующий ключ.
    derived_public = subprocess.run(
        ["ssh-keygen", "-y", "-P", "", "-f", str(private_key)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not public_key.exists():
        public_key.write_text(derived_public + "\n")
    if public_key.read_text().split()[:2] != derived_public.split()[:2]:
        raise SystemExit("Публичный и приватный ключи не соответствуют друг другу.")

    template = (root / "infra" / "cloud-init.yaml").read_text()
    rendered = template.replace("__LAB_SSH_PUBLIC_KEY__", derived_public)
    destination = lab / "cloud-init.yaml"
    if not destination.exists() or destination.read_text() != rendered:
        destination.write_text(rendered)
    print("Локальный SSH-ключ проверен; .lab/cloud-init.yaml готов. VM не создавалась.")


if __name__ == "__main__":
    main()
