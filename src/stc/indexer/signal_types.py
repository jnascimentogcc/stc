from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    """One fact that can link applications. `key` is what two applications must share for a
    relation to exist (normalized path, topic, `engine://host:port/db`, package name)."""

    kind: str  # http | messaging | database | library
    role: str  # provides | consumes | publishes | subscribes | uses
    key: str
    host: str | None  # http consumers: host or env-var token; databases: host
    detail: str  # what the code says, never credentials
    start_line: int
    end_line: int
