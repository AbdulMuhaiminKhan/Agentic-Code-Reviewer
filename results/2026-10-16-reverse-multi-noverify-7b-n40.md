# Backtest: ollama:qwen2.5-coder:7b · mode=multi · verify=0 · input=reverse

| Metric | Value | 95% CI |
|---|---|---|
| Recall, strict (right issue, right lines) | **7/40 = 18%** | 9%-32% |
| Recall, loose (right issue, same file) | 7/40 = 18% | 9%-32% |
| Avg findings per vulnerable snippet | 2.17 | |
| Strict catches the verifier rejected (would be hits with ACR_VERIFY=0) | 0/40 | |
| Latency per review p50 / max | 35.8s / 116.0s (over 40 reviews) | |

| Advisory | CWE | Caught (strict) | Fixed version flagged |
|---|---|---|---|
| CVE-2009-2659 | CWE-22 | ✅ | n/a |
| CVE-2013-0305 | CWE-200 | ✅ | n/a |
| CVE-2020-24583 | CWE-276 | ❌ | n/a |
| CVE-2016-6186 | CWE-79 | ❌ | n/a |
| CVE-2021-45115 | CWE-400 | ❌ | n/a |
| CVE-2018-6188 | CWE-200 | ❌ | n/a |
| CVE-2014-3730 | CWE-20 | ❌ | n/a |
| CVE-2007-0405 |  | ❌ | n/a |
| CVE-2026-53878 | CWE-144 | ❌ | n/a |
| CVE-2025-64459 | CWE-89 | ❌ | n/a |
| CVE-2010-4534 | CWE-20 | ❌ | n/a |
| CVE-2022-34265 | CWE-89 | ❌ | n/a |
| CVE-2015-5964 |  | ❌ | n/a |
| CVE-2018-7537 | CWE-185 | ❌ | n/a |
| CVE-2013-1665 | CWE-200, CWE-611 | ❌ | n/a |
| CVE-2011-0696 | CWE-352 | ✅ | n/a |
| CVE-2026-15307 | CWE-73 | ❌ | n/a |
| CVE-2019-14234 | CWE-89 | ✅ | n/a |
| CVE-2014-0483 | CWE-200 | ✅ | n/a |
| CVE-2024-42005 | CWE-89 | ❌ | n/a |
| CVE-2025-13372 | CWE-89 | ❌ | n/a |
| CVE-2022-28346 | CWE-89 | ❌ | n/a |
| CVE-2016-2048 | CWE-284 | ❌ | n/a |
| CVE-2012-3443 | CWE-20, CWE-400 | ❌ | n/a |
| CVE-2024-24680 | CWE-770 | ❌ | n/a |
| CVE-2018-14574 | CWE-601 | ❌ | n/a |
| CVE-2026-15830 | CWE-674 | ❌ | n/a |
| CVE-2022-36359 | CWE-494 | ❌ | n/a |
| CVE-2022-28347 | CWE-89 | ❌ | n/a |
| CVE-2008-3909 | CWE-352 | ❌ | n/a |
| CVE-2007-5712 | CWE-400 | ❌ | n/a |
| CVE-2022-22818 | CWE-79 | ❌ | n/a |
| CVE-2014-0473 | CWE-200 | ❌ | n/a |
| CVE-2022-41323 | CWE-1333 | ❌ | n/a |
| CVE-2024-39614 | CWE-130, CWE-770 | ❌ | n/a |
| CVE-2012-3442 | CWE-79 | ✅ | n/a |
| CVE-2024-56374 | CWE-770 | ✅ | n/a |
| CVE-2017-7233 | CWE-601 | ❌ | n/a |
| CVE-2025-32873 | CWE-770 | ❌ | n/a |
| CVE-2014-0472 | CWE-94 | ❌ | n/a |