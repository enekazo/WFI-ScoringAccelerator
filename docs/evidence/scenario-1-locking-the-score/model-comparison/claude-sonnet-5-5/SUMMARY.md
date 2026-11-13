# claude-sonnet-5-5 — model comparison NOT RUN (deployment blocked)

Catalogue entry (centralus, account `example-foundry-account`): `claude-sonnet-5-5`, format `Anthropic`,
versions `1` (GlobalStandard) and `2` (GlobalStandard, DataZoneStandard). Attempted: version 2, GlobalStandard, capacity 50,
deployment name `claude-sonnet-5-5`, Entra auth (az CLI) only.

## Attempts (CLI lacked a required field, so retried via ARM REST)

1. `az cognitiveservices account deployment create ...`:
   `InvalidModelProviderData: ModelProviderData is required for Anthropic model deployments but was not provided. Please provide all required fields: industry, organizationName, and countryCode.`
   REST PUT with `industry=technology, organizationName=Microsoft, countryCode=US` returned the same error on
   api-version 2025-06-01 and 2025-09-01.
2. REST PUT, api-version `2025-10-01-preview` (accepts modelProviderData) - the real blocker:

```
UserError: Error occurred when subscribing to Marketplace: Marketplace Subscription purchase eligibility check failed, error message [This subscription is internal or sandbox. Only $0.00 products or test products can be purchased. Please select a different subscription to purchase paid, non-test offers Marketplace purchases are disabled for this subscription '00000000-0000-4000-8000-000000000003' due to policy restrictions. To proceed, contact your tenant administrator and ask them to enable the Marketplace purchase policy Details: UnsupportedForInternalSubscription: This subscription is internal or sandbox. Only $0.00 products or test products can be purchased. Please select a different subscription to purchase paid, non-test offers; EAFreeOnlyWithPaidProduct: Marketplace purchases are disabled for this subscription '00000000-0000-4000-8000-000000000003' due to policy restrictions. To proceed, contact your tenant administrator and ask them to enable the Marketplace purchase policy].
```

## Outcome
- No deployment was created; nothing to delete. gpt-4.1 / gpt-4.1-mini untouched.
- No experiment runs, no tokens used, cost 0 USD. No headline numbers for any arm.
- Per instructions, no workaround (no subscription change, no marketplace enablement).
- To unblock: a subscription with Marketplace purchases allowed, or tenant admin enabling the policy.
- The Anthropic adapter was not written.
