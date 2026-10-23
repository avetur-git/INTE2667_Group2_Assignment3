Load test run 2026-10-23 00:08: 50 concurrent terminals, 300 votes per run, each replica limited to 1 CPU.

| Replicas | Votes recorded | Failed | Time (s) | Throughput (votes/s) | Speed-up | Vote flow median (ms) | Vote flow p95 (ms) | Cast median (ms) |
|---|---|---|---|---|---|---|---|---|
| 1 | 300 | 0 | 192.1 | 1.56 | 1.00x | 31180 | 34277 | 7538 |
| 2 | 300 | 0 | 116.5 | 2.57 | 1.65x | 18404 | 22705 | 6826 |
| 3 | 300 | 0 | 93.5 | 3.21 | 2.05x | 14306 | 21094 | 5917 |
