from .core import retry
from ..utils import helpers


class PaymentGateway:
    """Handles retry and refund flow for stripe charges."""

    def refund(self, charge_id):
        return retry(charge_id)

    def capture(self):
        return helpers.format_amount(1)
