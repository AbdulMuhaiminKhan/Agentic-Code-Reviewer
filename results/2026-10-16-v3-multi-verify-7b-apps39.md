# Backtest: ollama:qwen2.5-coder:7b · mode=multi · verify=1 · input=snapshot

| Metric | Value | 95% CI |
|---|---|---|
| Recall, strict (right issue, right lines) | **2/39 = 5%** | 1%-17% |
| Recall, loose (right issue, same file) | 2/39 = 5% | 1%-17% |
| False alarm on fixed code (still flags the patched bug) | 2/39 = 5% | 1%-17% |
| Discriminating catches (vulnerable caught AND fixed clean) | **1/39 = 3%** | 0%-13% |
| Avg findings per fixed (clean) snippet, i.e. noise | 2.51 | |
| Avg findings per vulnerable snippet | 2.90 | |
| Strict catches the verifier rejected (would be hits with ACR_VERIFY=0) | 1/39 | |
| Latency per review p50 / max | 62.6s / 328.4s (over 78 reviews) | |

| Advisory | CWE | Caught (strict) | Fixed version flagged |
|---|---|---|---|
| GHSA-fj52-5g4h-gmq8 | CWE-613 | ❌ | no |
| CVE-2023-34110 | CWE-209 | ❌ | no |
| CVE-2025-61773 | CWE-74, CWE-79, CWE-94, CWE-116 | ❌ | no |
| CVE-2024-24808 | CWE-601 | ❌ | no |
| GHSA-3wwm-hjv7-23r3 | CWE-94 | ❌ | no |
| CVE-2026-33992 | CWE-918 | ❌ | no |
| CVE-2025-58065 | CWE-287 | ❌ | no |
| CVE-2024-21644 | CWE-284 | ❌ | no |
| CVE-2026-35463 | CWE-78 | ❌ | no |
| CVE-2026-33509 | CWE-269 | ❌ | no |
| CVE-2021-29621 | CWE-203 | ❌ | no |
| GHSA-68w4-83fh-f2w8 | CWE-287 | ❌ | no |
| CVE-2023-0057 | CWE-1021 | ❌ | no |
| CVE-2025-54802 | CWE-22 | ✅ | ⚠️ yes |
| CVE-2021-32805 | CWE-601 | ❌ | no |
| CVE-2024-47821 | CWE-78 | ❌ | no |
| CVE-2024-21645 | CWE-74 | ❌ | no |
| CVE-2025-32962 | CWE-601 | ❌ | no |
| CVE-2023-0227 | CWE-613 | ❌ | no |
| CVE-2023-0509 | CWE-295 | ❌ | no |
| CVE-2025-7346 | CWE-284, CWE-290 | ❌ | no |
| CVE-2024-45314 | CWE-525 | ❌ | no |
| CVE-2026-35464 | CWE-502, CWE-863 | ❌ | no |
| CVE-2023-0297 | CWE-94 | ❌ | no |
| CVE-2021-41265 | CWE-287 | ❌ | no |
| CVE-2025-55156 | CWE-89 | ✅ | no |
| GHSA-r44w-v6gf-x3p6 | CWE-287 | ❌ | no |
| GHSA-fr26-jjhm-638c | CWE-22 | ❌ | no |
| CVE-2024-25128 | CWE-287 | ❌ | no |
| CVE-2025-54140 | CWE-22 | ❌ | no |
| CVE-2023-0055 | CWE-319, CWE-614 | ❌ | no |
| CVE-2022-21659 | CWE-203, CWE-204 | ❌ | no |
| CVE-2023-47890 | CWE-22 | ❌ | ⚠️ yes |
| CVE-2026-41133 | CWE-613 | ❌ | no |
| CVE-2026-48484 | CWE-20, CWE-400 | ❌ | no |
| CVE-2026-75597 | CWE-209, CWE-306 | ❌ | no |
| CVE-2023-0434 | CWE-20 | ❌ | no |
| CVE-2026-35187 | CWE-918 | ❌ | no |
| CVE-2024-22416 | CWE-352 | ❌ | no |