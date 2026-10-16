# Backtest: ollama:qwen2.5-coder:7b · mode=multi · verify=1 · input=snapshot

| Metric | Value | 95% CI |
|---|---|---|
| Recall, strict (right issue, right lines) | **0/5 = 0%** | 0%-43% |
| Recall, loose (right issue, same file) | 0/5 = 0% | 0%-43% |
| False alarm on fixed code (still flags the patched bug) | 0/5 = 0% | 0%-43% |
| Discriminating catches (vulnerable caught AND fixed clean) | **0/5 = 0%** | 0%-43% |
| Avg findings per fixed (clean) snippet, i.e. noise | 1.80 | |
| Avg findings per vulnerable snippet | 1.00 | |
| Latency per review p50 / max | 28.0s / 99.3s | |

| Advisory | CWE | Caught (strict) | Fixed version flagged |
|---|---|---|---|
| CVE-2009-2659 | CWE-22 | ❌ | no |
| CVE-2013-0305 | CWE-200 | ❌ | no |
| CVE-2020-24583 | CWE-276 | ❌ | no |
| CVE-2016-6186 | CWE-79 | ❌ | no |
| CVE-2021-45115 | CWE-400 | ❌ | no |