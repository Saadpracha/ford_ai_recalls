import csv
import json
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    username: str
    password: str

    def to_playwright(self) -> dict[str, str]:
        return {
            "server": f"http://{self.host}:{self.port}",
            "username": self.username,
            "password": self.password,
        }

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


def load_proxies(csv_path: Path) -> list[Proxy]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"Proxy CSV not found: {csv_path}")

    proxies: list[Proxy] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            proxies.append(
                Proxy(
                    host=row["Host"].strip(),
                    port=int(row["Port"].strip()),
                    username=row["User"].strip(),
                    password=row["Pass"].strip(),
                )
            )

    if not proxies:
        raise ValueError(f"No proxies found in {csv_path}")

    return proxies


def pick_random_proxy(proxies: list[Proxy]) -> Proxy:
    """Pick a random proxy from the list (new IP each run)."""
    return secrets.choice(proxies)


def pick_proxy(proxies: list[Proxy], index: int | None = None) -> Proxy:
    if index is not None:
        return proxies[index % len(proxies)]
    return pick_random_proxy(proxies)


def save_proxy_session(session_path: Path, proxy: Proxy | None) -> None:
    """Persist proxy used at login so scrape runs reuse the same IP."""
    session_path.parent.mkdir(parents=True, exist_ok=True)
    if proxy is None:
        data = {"no_proxy": True}
    else:
        data = {
            "no_proxy": False,
            "host": proxy.host,
            "port": proxy.port,
            "user": proxy.username,
            "pass": proxy.password,
        }
    session_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_proxy_session(session_path: Path) -> Proxy | None:
    """
    Load saved proxy for session reuse.
    Returns None if saved session was direct (no proxy).
    Raises FileNotFoundError if no session file exists.
    """
    data = json.loads(session_path.read_text(encoding="utf-8"))
    if data.get("no_proxy"):
        return None
    return Proxy(
        host=data["host"],
        port=int(data["port"]),
        username=data["user"],
        password=data["pass"],
    )


def has_proxy_session(session_path: Path) -> bool:
    return session_path.is_file()
