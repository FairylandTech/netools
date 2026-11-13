from ipaddress import ip_address
from typing import Annotated

from pydantic import AfterValidator


def normalize_ip(value: str) -> str:
    if "%" in value:
        raise ValueError("IPv6 scope ids are not supported")
    return str(ip_address(value))


IPAddress = Annotated[str, AfterValidator(normalize_ip)]
