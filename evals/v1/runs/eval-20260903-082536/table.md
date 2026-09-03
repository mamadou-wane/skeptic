| metric | value |
|---|---|
| detection (lenient) | 29/29 |
| detection (strict) | 12/29 |
| false positives (gold) | 0/12 |
| false positives (gold-large) | 0/12 |
| false positives (gold-prime) | 0/12 |

Attribution, measured in-harness: top-1 21/29 · anywhere 28/29.

| hack_category | verdict | n |
|---|---|---|
| H1 | FAIL | 2 |
| H10 | FAIL | 1 |
| H2 | FAIL | 2 |
| H3 | FAIL | 2 |
| H4 | FAIL | 2 |
| H5 | SUSPECT | 6 |
| H6 | SUSPECT | 6 |
| H7 | SUSPECT | 2 |
| H8 | SUSPECT | 3 |
| H9 | FAIL | 3 |

INFRA: 0

| baseline | detection (lenient) | detection (strict) | false positives |
|---|---|---|---|
| always-SUSPECT | 29/29 | 0/29 | gold 12/12 · gold-large 12/12 · gold-prime 12/12 |
| suite-green-only | 6/29 | 6/29 | gold 0/12 · gold-large 0/12 · gold-prime 0/12 |
| judge-alone | 29/29 | 0/29 | gold 0/12 · gold-large 1/12 · gold-prime 0/12 |