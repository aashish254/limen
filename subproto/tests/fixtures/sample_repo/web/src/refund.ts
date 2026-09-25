export function retryRefund(chargeId) {
  return fetch(`/payments/${chargeId}/refund`, { method: "POST" });
}

export class RetryPolicy {
  constructor(max) {
    this.max = max;
  }
}
