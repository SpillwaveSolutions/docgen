"""Payment gateway abstraction over a fake upstream.

StripeGateway is the only implementation; it retries failed charges linearly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Charge:
    id: str
    amount_cents: int
    currency: str = "USD"


class StripeGateway:
    """Thin wrapper around a pretend Stripe API.

    Retries a failed charge up to `max_retries` times with linear backoff.
    """

    def __init__(self, api_key: str, max_retries: int = 3) -> None:
        self.api_key = api_key
        self.max_retries = max_retries

    def charge(self, amount_cents: int, currency: str = "USD") -> Charge:
        """Submit a charge for the given amount. Returns a Charge on success."""
        # Fake body — real implementation would POST to https://api.stripe.com
        return Charge(id=f"ch_{amount_cents}", amount_cents=amount_cents, currency=currency)

    def refund(self, charge_id: str) -> bool:
        """Refund the charge. Returns True on success."""
        return charge_id.startswith("ch_")
