| model | arm | error | class | rows |
|---|---|---|---|---|
| gpt-4.1 | A+ | RateLimitError 429 | infrastructure | 1 |
| gpt-4.1 | B | RateLimitError 429 | infrastructure | 5 |
| gpt-4.1-mini | A | APITimeoutError: Request timed out. | infrastructure | 5 |
| gpt-4.1-mini | A+ | APITimeoutError: Request timed out. | infrastructure | 1 |
| gpt-4.1-mini | B | RateLimitError 429 | infrastructure | 8 |
| gpt-6.1-sol | A | APITimeoutError: Request timed out. | infrastructure | 14 |
| gpt-6.1-sol | A | ClientAuthenticationError: ERROR: The command failed with an unexpected error. H | infrastructure | 1 |
| gpt-6.1-sol | B | APIConnectionError: Connection error. | infrastructure | 2 |
| gpt-6.1-sol | B | APITimeoutError: Request timed out. | infrastructure | 13 |
| gpt-6.1-terra | A | APITimeoutError: Request timed out. | infrastructure | 6 |
| gpt-6.1-terra | B | APITimeoutError: Request timed out. | infrastructure | 4 |
| grok-4.7 | A | APITimeoutError: Request timed out. | infrastructure | 18 |
| phi-4 | A | APITimeoutError: Request timed out. | infrastructure | 2 |

Per batch: batch_02=32, batch_03=11, batch_04=37
Total errored: 80; infrastructure: 80; genuine: 0
