| Method | Configuration | Bytes/raw | P95 pos. (m) | Mean vel. (m/s) | Max vel. (m/s) | Semantic F1 | Guarantee |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Uniform linear | stride 10 | 0.4802 | 0.4973 | 0.1352 | 18.461 | 0.8816 | none |
| Uniform Hermite | stride 10 | 0.4806 | 0.4788 | 0.4722 | 34.160 | 0.5770 | none |
| Fixed-interval linear | 1,000 ms | 0.4806 | 0.4973 | 0.1352 | 18.461 | 0.8816 | none |
| RDP linear | 0.05 m | 0.4419 | 0.9455 | 0.1940 | 17.148 | 0.8783 | geometric path tol. only (0.05 m) |
| **Position-bounded linear** | 0.10 m | 0.5074 | 0.0918 | 0.0814 | 17.148 | 0.9121 | pos. <= 0.10 m |
| **Position/velocity hybrid** | 0.10 m / 1.00 m/s | 0.4913 | 0.0806 | 0.1166 | 1.000 | 0.8860 | pos. <= 0.10 m; repr. vel. <= 1.00 m/s |
| Unconstrained Hermite | 0.10 m | 0.4699 | 0.0807 | 0.2745 | 26.062 | 0.7274 | pos. <= 0.10 m |
