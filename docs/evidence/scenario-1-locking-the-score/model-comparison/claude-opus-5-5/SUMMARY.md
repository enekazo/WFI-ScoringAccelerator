# claude-opus-5-5 model comparison: NOT RUN (deployment blocked by policy)

No experiment arms (A raw, A+ hint, B pinned + verifier, weight sensitivity) were run. There are no headline numbers, no token usage and no cost (0 USD). No model call was made.

## Catalogue entry found
Account `example-foundry-account` (centralus) lists, format `Anthropic`:
- `claude-opus-5-5`, version `1` (GlobalStandard)
- `claude-opus-5-5`, version `2` (GlobalStandard, DataZoneStandard)
- `claude-opus-5-5-fast`, version `2`

Attempted: `claude-opus-5-5` version `2`, GlobalStandard, capacity 40 (40K TPM), deployment name `claude-opus-5-5`, Entra auth via `az`.

## Exact errors
1. `az cognitiveservices account deployment create` (and ARM API 2025-06-01 / 2025-09-01):
   `(InvalidModelProviderData) ModelProviderData is required for Anthropic model deployments but was not provided. Please provide all required fields: industry, organizationName, and countryCode.`
   (These APIs ignore the field, so this is not a policy block.)
2. ARM PUT with API `2025-10-01-preview`, with `modelProviderData` {industry: technology, organizationName: Microsoft, countryCode: US}:
   `UserError: Error occurred when subscribing to Marketplace: Marketplace Subscription purchase eligibility check failed, error message [This subscription is internal or sandbox. Only $0.00 products or test products can be purchased. Please select a different subscription to purchase paid, non-test offers Marketplace purchases are disabled for this subscription '00000000-0000-4000-8000-000000000003' due to policy restrictions. To proceed, contact your tenant administrator and ask them to enable the Marketplace purchase policy Details: UnsupportedForInternalSubscription: This subscription is internal or sandbox. Only $0.00 products or test products can be purchased. Please select a different subscription to purchase paid, non-test offers; EAFreeOnlyWithPaidProduct: Marketplace purchases are disabled for this subscription '00000000-0000-4000-8000-000000000003' due to policy restrictions. To proceed, contact your tenant administrator and ask them to enable the Marketplace purchase policy]`

Also hit a transient `RequestConflict` (parallel deployments by other sessions); retried.

## Caveats
Per instructions, no workaround was attempted (no subscription change, no marketplace enablement). No Anthropic adapter was written. No deployment was created, so none needs deleting. To proceed, a tenant admin must enable Marketplace purchases for this subscription, or a different authorised subscription must be used.
