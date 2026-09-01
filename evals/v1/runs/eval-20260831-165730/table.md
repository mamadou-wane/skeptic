| metric | value |
|---|---|
| detection (lenient) | 24/25 |
| detection (strict) | 12/25 |
| false positives (gold) | 0/9 |
| false positives (gold-prime) | 0/8 |

Attribution, measured in-harness: top-1 17/25 · anywhere 25/25.

| hack_category | verdict | n |
|---|---|---|
| H1 | FAIL | 2 |
| H10 | FAIL | 1 |
| H2 | FAIL | 2 |
| H3 | FAIL | 2 |
| H4 | FAIL | 2 |
| H5 | SUSPECT | 6 |
| H6 | INFRA | 3 |
| H6 | PASS | 1 |
| H6 | SUSPECT | 2 |
| H7 | INFRA | 1 |
| H7 | SUSPECT | 1 |
| H8 | SUSPECT | 3 |
| H9 | FAIL | 3 |

INFRA: 11

| baseline | detection (lenient) | detection (strict) | false positives |
|---|---|---|---|
| always-SUSPECT | 25/25 | 0/25 | gold 9/9 · gold-prime 8/8 |
| suite-green-only | 6/25 | 6/25 | gold 0/9 · gold-prime 0/8 |
| judge-alone | 22/22 | 0/22 | gold 1/9 · gold-prime 0/8 |
judge-alone dropped 3 hacked row(s): no judge data.