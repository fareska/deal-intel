# Synthetic Deal Desk and Approval Policy

These rules are fictional and intended only for the AI Engineer exam dataset.

## Approval Rules

1. Discounts greater than 10 percent require Deal Desk approval before they are recommended internally or shared with a customer.
2. Discounts greater than 15 percent require both Deal Desk and sales leader approval.
3. Any negative renewal uplift requires Deal Desk approval, even if the percentage discount is below threshold.
4. Liability cap changes require legal approval before customer-facing language is generated.
5. Data retention, restricted research data, or customer-specific security language requires legal approval before it is shared externally.
6. Customer-facing concession language must not be generated unless the recommendation has an approved status.
7. Low-confidence recommendations, conflicting evidence, or missing source data must be routed to a human reviewer.
8. Restricted account sources may only be retrieved and summarized for users whose permission profile allows restricted account access.
9. Sensitive pricing notes may only be shown to users with `can_view_sensitive_pricing=true`.
10. If source access is denied, the system must not reveal a summary, title, quote, inferred fact, or metadata from the restricted source.
