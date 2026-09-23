from __future__ import annotations

import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup(level: int = logging.INFO) -> None:
    """Включить вывод логов."""
    logging.basicConfig(level=level, format=FORMAT)
