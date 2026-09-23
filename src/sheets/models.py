from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pandas as pd


@dataclass(frozen=True)
class Report:
    """Что сделал пайплайн — чтобы залогировать и показать."""

    rows_in: int
    rows_dropped: int
    rows_deduped: int
    rows_out: int
    bytes_in: int
    bytes_out: int
    seconds: float

    @property
    def ratio(self) -> float:
        return self.bytes_in / self.bytes_out if self.bytes_out else 0.0

    def text(self) -> str:
        return (
            f"строк на входе: {self.rows_in:,}\n"
            f"отброшено битых: {self.rows_dropped:,}\n"
            f"снято дублей:    {self.rows_deduped:,}\n"
            f"строк в витрине: {self.rows_out:,}\n"
            f"объём: {self.bytes_in / 1024 / 1024:.2f} МБ -> "
            f"{self.bytes_out / 1024 / 1024:.2f} МБ (в {self.ratio:.1f}x компактнее)\n"
            f"время: {self.seconds:.2f} с"
        ).replace(",", " ")


@dataclass(frozen=True)
class Synced:
    mart: pd.DataFrame
    report: Report
    rows_written: int
    csv: Path | None
