from pathlib import Path


def read_lua(anchor: str, name: str) -> str:
    """Read ``lua/<name>`` next to the module given by ``anchor`` (pass ``__file__``)."""
    return (Path(anchor).with_name("lua") / name).read_text()
