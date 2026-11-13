from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ServiceError(Exception):
    """A client-facing error with a stable code; never carries upstream details."""

    def __init__(self, code: str, message: str, status_code: int = 503):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def detail(self) -> ErrorDetail:
        return ErrorDetail(code=self.code, message=self.message)
