"""Transaction reporting.

Report aggregates Charges into a human-readable summary. Used by the CLI and
by downstream audit tooling.
"""

from __future__ import annotations

from tiny.payments.gateway import Charge


class Report:
    """Aggregates a list of charges into a totalled summary."""

    def __init__(self, charges: list[Charge]) -> None:
        self.charges = list(charges)

    def total_cents(self) -> int:
        """Return the sum of all charge amounts in cents."""
        return sum(c.amount_cents for c in self.charges)

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return f"{len(self.charges)} charges totalling {self.total_cents() / 100:.2f}"
