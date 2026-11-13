from netools.core.errors import ServiceError


class AdmissionGate:
    """Bounded per-process admission for in-flight lookups.

    The check and the increment happen without an await in between, so admission
    is atomic on a single event loop.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0

    def __enter__(self) -> "AdmissionGate":
        if self.active >= self.limit:
            raise ServiceError("overloaded", "The service is busy; retry later")
        self.active += 1
        return self

    def __exit__(self, *_: object) -> None:
        self.active -= 1
