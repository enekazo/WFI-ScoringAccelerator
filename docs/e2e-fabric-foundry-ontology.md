# Fabric data, Foundry agent and ontology: how to reproduce

Use the [canonical deployment instructions](../deploy/README.md) for configuration,
preview, installation and uninstall. The installer requires an existing active Fabric
capacity (`F4+`) and does not create or resize capacities; it reuses the configured
Fabric workspace when present or creates it on that capacity in stage 4 when absent.
The default deployment size is 150 parcels, configurable through `parcelCount`.

## Reproduce / One-click install

Installation and testing are different operations. Use [independent post-deployment
checks](testing.md#independent-post-deployment-checks) to inspect existing resources.
`fabric_load_chain.py` writes data, and `fabric_graph_model_fix.py` without its
verification or dry-run mode applies changes and refreshes the graph: neither is
a read-only health check.

The historical relationship results cover **50 parcels and 150 CrewAccess entries**,
not the default size of a new deployment. GQL C1-C5 expected answers must be calculated
from the current receipt dataset and policy, not copied from the old report.

For all three experiment methods, real CLI arguments, repeat counts, source selection,
separate output locations and live/billable prerequisites, see
[experiment reproduction](experiment.md). The [Scenario 2 report](reports/scenario-2-ontology.md)
retains the recorded results, inconclusive usefulness result and original method limits.

Keep the receipt agent and Fabric IQ agent separate. The approved Function-hosted
score-verification path has a [runtime release gate](azure-mapping.md#verified-baseline-and-release-gates);
historical ontology answers alone cannot satisfy it.
