# Search eval 2026-10-10 — pilot-v0 (known-item)

- Results: `/Users/mazhewitt/mnt/shoebox/results/vally/pilot-v0-search/2026-10-10T15-11-33-109Z/results.jsonl`
- Routes file: `/Users/mazhewitt/projects/shoebox/evals/vally/generated/pilot-v0-search/routes.json`
- Queries: 39 (owner_notes); complete: yes

## Routes

Metrics are expectations over random tie orders. MRR tie range = MRR if every tie broke against / for the relevant item; ties = queries whose first relevant rank depends on the tie order.

| route | n | MRR | MRR 95% CI | MRR tie range | ties | hit@1 | hit@5 | hit@10 | nDCG@10 | nDCG@20 | recall@20 | mean rank | median rank | params_hash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| eg2-blend:g31b | 39 | 0.987 | 0.962–1.000 | 0.987–0.987 | 0 | 0.974 | 1.000 | 1.000 | 0.991 | 0.991 | 1.000 | 1.0 | 1.0 | `9e7654d71cf162b3` |
| eg2-blend:e4b-bf16 | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `9583b60fab859250` |
| eg2-blend:e4b-q4 | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `2aa5a2d55d483507` |
| eg2-blend:gemini-medium | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `452048e39061e756` |
| eg2-image@256 | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `1a7d19717548daf5` |
| eg2-image@768 | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `b2c2607a2065d858` |
| eg2-caption:gemini-medium | 39 | 0.981 | 0.942–1.000 | 0.981–0.981 | 0 | 0.974 | 1.000 | 1.000 | 0.985 | 0.985 | 1.000 | 1.1 | 1.0 | `445b20cd7a0c95f5` |
| eg2-blend:gemini-low | 39 | 0.974 | 0.936–1.000 | 0.974–0.974 | 0 | 0.949 | 1.000 | 1.000 | 0.981 | 0.981 | 1.000 | 1.1 | 1.0 | `7beba552273c8a4e` |
| eg2-caption:g31b | 39 | 0.974 | 0.936–1.000 | 0.974–0.974 | 0 | 0.949 | 1.000 | 1.000 | 0.981 | 0.981 | 1.000 | 1.1 | 1.0 | `f415e2e0e2a03503` |
| eg2-blend:g12b-q4 | 39 | 0.970 | 0.923–1.000 | 0.970–0.970 | 0 | 0.949 | 1.000 | 1.000 | 0.978 | 0.978 | 1.000 | 1.1 | 1.0 | `5f10c712d37ecf9c` |
| eg2-caption:g12b-q4 | 39 | 0.938 | 0.872–0.987 | 0.938–0.938 | 0 | 0.897 | 1.000 | 1.000 | 0.954 | 0.954 | 1.000 | 1.2 | 1.0 | `bfc4d95758909470` |
| eg2-caption:gemini-low | 39 | 0.936 | 0.873–0.987 | 0.936–0.936 | 0 | 0.897 | 0.974 | 0.974 | 0.946 | 0.946 | 0.974 | 2.2 | 1.0 | `8551cc1d4810c16c` |
| hybrid:gemini-medium | 39 | 0.934 | 0.876–0.983 | 0.915–0.953 | 3 | 0.885 | 1.000 | 1.000 | 0.951 | 0.951 | 1.000 | 1.2 | 1.0 | `ed522a1c9fd57b1e` |
| eg2-caption:e4b-bf16 | 39 | 0.926 | 0.844–0.987 | 0.926–0.926 | 0 | 0.897 | 0.949 | 0.949 | 0.930 | 0.943 | 1.000 | 1.8 | 1.0 | `3ae9678435814145` |
| hybrid:e4b-q4 | 39 | 0.926 | 0.863–0.976 | 0.912–0.940 | 3 | 0.872 | 1.000 | 1.000 | 0.945 | 0.945 | 1.000 | 1.2 | 1.0 | `cf74718d30d99096` |
| hybrid:g12b-q4 | 39 | 0.921 | 0.848–0.981 | 0.902–0.940 | 3 | 0.885 | 0.974 | 1.000 | 0.940 | 0.940 | 1.000 | 1.3 | 1.0 | `2b541a14663381a4` |
| eg2-caption:e4b-q4 | 39 | 0.920 | 0.839–0.983 | 0.920–0.920 | 0 | 0.897 | 0.949 | 0.974 | 0.932 | 0.938 | 1.000 | 1.6 | 1.0 | `5b9a56ed73999ff5` |
| hybrid:gemini-low | 39 | 0.910 | 0.840–0.968 | 0.885–0.936 | 4 | 0.846 | 1.000 | 1.000 | 0.933 | 0.933 | 1.000 | 1.3 | 1.0 | `dda8f3426745c63d` |
| hybrid:e4b-bf16 | 39 | 0.904 | 0.833–0.962 | 0.885–0.923 | 3 | 0.833 | 1.000 | 1.000 | 0.928 | 0.928 | 1.000 | 1.3 | 1.0 | `92ead1ca115db18d` |
| hybrid:g31b | 39 | 0.899 | 0.823–0.965 | 0.886–0.912 | 2 | 0.846 | 0.949 | 0.974 | 0.916 | 0.923 | 1.000 | 1.6 | 1.0 | `1c1f13796fab5c9b` |
| fts:gemini-medium | 39 | 0.890 | 0.809–0.962 | 0.888–0.892 | 1 | 0.821 | 0.974 | 1.000 | 0.917 | 0.917 | 1.000 | 1.4 | 1.0 | `6847fb400d9d72cb` |
| fts:gemini-low | 39 | 0.850 | 0.752–0.942 | 0.847–0.861 | 3 | 0.795 | 0.951 | 0.957 | 0.875 | 0.878 | 0.969 | 2.6 | 1.0 | `3dbfe74cd2d99b4f` |
| fts:g12b-q4 | 39 | 0.836 | 0.734–0.923 | 0.834–0.859 | 1 | 0.770 | 0.952 | 0.954 | 0.863 | 0.871 | 0.986 | 2.4 | 1.0 | `7b9286bf1aaaf91d` |
| fts:g31b | 39 | 0.825 | 0.725–0.913 | 0.821–0.829 | 3 | 0.744 | 0.906 | 0.949 | 0.853 | 0.861 | 0.981 | 2.6 | 1.0 | `fe37ffe57296f93d` |
| fts:e4b-q4 | 39 | 0.807 | 0.703–0.902 | 0.801–0.834 | 3 | 0.719 | 0.952 | 0.980 | 0.849 | 0.850 | 0.986 | 2.2 | 1.0 | `238459d531d2ccb5` |
| fts:e4b-bf16 | 39 | 0.749 | 0.638–0.854 | 0.741–0.809 | 6 | 0.668 | 0.854 | 0.940 | 0.792 | 0.797 | 0.957 | 3.6 | 1.0 | `9b1a9ae112193e0d` |
| eg2-blend@w0.3:g31b (sensitivity) | 39 | 0.987 | 0.962–1.000 | 0.987–0.987 | 0 | 0.974 | 1.000 | 1.000 | 0.991 | 0.991 | 1.000 | 1.0 | 1.0 | `ef2192e7734c1af9` |
| eg2-blend@w0.7:g31b (sensitivity) | 39 | 0.987 | 0.962–1.000 | 0.987–0.987 | 0 | 0.974 | 1.000 | 1.000 | 0.991 | 0.991 | 1.000 | 1.0 | 1.0 | `30ef6797772b9b10` |
| eg2-blend@w0.7:gemini-low (sensitivity) | 39 | 0.987 | 0.962–1.000 | 0.987–0.987 | 0 | 0.974 | 1.000 | 1.000 | 0.991 | 0.991 | 1.000 | 1.0 | 1.0 | `f86d63349ca5a756` |
| eg2-blend@w0.3:gemini-medium (sensitivity) | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `cde071c89050aa14` |
| eg2-blend@w0.7:e4b-bf16 (sensitivity) | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `047a24efcd210015` |
| eg2-blend@w0.7:e4b-q4 (sensitivity) | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `d45ac563bd06729a` |
| eg2-blend@w0.7:g12b-q4 (sensitivity) | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `8ecc20f8aba79ee2` |
| eg2-blend@w0.7:gemini-medium (sensitivity) | 39 | 0.983 | 0.949–1.000 | 0.983–0.983 | 0 | 0.974 | 1.000 | 1.000 | 0.987 | 0.987 | 1.000 | 1.1 | 1.0 | `7883e37c18fd50fa` |
| eg2-blend@w0.3:gemini-low (sensitivity) | 39 | 0.974 | 0.936–1.000 | 0.974–0.974 | 0 | 0.949 | 1.000 | 1.000 | 0.981 | 0.981 | 1.000 | 1.1 | 1.0 | `8aeb1b74892486cf` |
| eg2-blend@w0.3:e4b-bf16 (sensitivity) | 39 | 0.970 | 0.923–1.000 | 0.970–0.970 | 0 | 0.949 | 1.000 | 1.000 | 0.978 | 0.978 | 1.000 | 1.1 | 1.0 | `3f3fe2d8599774ab` |
| eg2-blend@w0.3:g12b-q4 (sensitivity) | 39 | 0.966 | 0.915–1.000 | 0.966–0.966 | 0 | 0.949 | 1.000 | 1.000 | 0.974 | 0.974 | 1.000 | 1.1 | 1.0 | `534786a2e842802e` |
| eg2-blend@w0.3:e4b-q4 (sensitivity) | 39 | 0.943 | 0.876–1.000 | 0.943–0.943 | 0 | 0.923 | 0.974 | 1.000 | 0.957 | 0.957 | 1.000 | 1.3 | 1.0 | `e374c86d9c6ac5d3` |

## Pool coverage

judged@k = the fraction of each route's top k that carries a relevance judgment (only the noted item(s) were judged; everything else is assumed non-relevant). Mean over queries.

| route | judged@1 | judged@5 | judged@10 | judged@20 |
|---|---|---|---|---|
| eg2-blend:g31b | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend:e4b-bf16 | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend:e4b-q4 | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend:gemini-medium | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-image@256 | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-image@768 | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-caption:gemini-medium | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend:gemini-low | 0.949 | 0.231 | 0.115 | 0.058 |
| eg2-caption:g31b | 0.949 | 0.231 | 0.115 | 0.058 |
| eg2-blend:g12b-q4 | 0.949 | 0.231 | 0.115 | 0.058 |
| eg2-caption:g12b-q4 | 0.897 | 0.231 | 0.115 | 0.058 |
| eg2-caption:gemini-low | 0.897 | 0.226 | 0.113 | 0.056 |
| hybrid:gemini-medium | 0.885 | 0.231 | 0.115 | 0.058 |
| eg2-caption:e4b-bf16 | 0.897 | 0.221 | 0.110 | 0.058 |
| hybrid:e4b-q4 | 0.872 | 0.231 | 0.115 | 0.058 |
| hybrid:g12b-q4 | 0.885 | 0.221 | 0.115 | 0.058 |
| eg2-caption:e4b-q4 | 0.897 | 0.221 | 0.113 | 0.058 |
| hybrid:gemini-low | 0.846 | 0.231 | 0.115 | 0.058 |
| hybrid:e4b-bf16 | 0.833 | 0.231 | 0.115 | 0.058 |
| hybrid:g31b | 0.846 | 0.221 | 0.113 | 0.058 |
| fts:gemini-medium | 0.821 | 0.226 | 0.115 | 0.058 |
| fts:gemini-low | 0.795 | 0.221 | 0.111 | 0.056 |
| fts:g12b-q4 | 0.770 | 0.216 | 0.111 | 0.057 |
| fts:g31b | 0.744 | 0.207 | 0.110 | 0.057 |
| fts:e4b-q4 | 0.719 | 0.221 | 0.113 | 0.057 |
| fts:e4b-bf16 | 0.668 | 0.196 | 0.109 | 0.056 |
| eg2-blend@w0.3:g31b | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.7:g31b | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.7:gemini-low | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.3:gemini-medium | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.7:e4b-bf16 | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.7:e4b-q4 | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.7:g12b-q4 | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.7:gemini-medium | 0.974 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.3:gemini-low | 0.949 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.3:e4b-bf16 | 0.949 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.3:g12b-q4 | 0.949 | 0.231 | 0.115 | 0.058 |
| eg2-blend@w0.3:e4b-q4 | 0.923 | 0.226 | 0.115 | 0.058 |

## Best caption-based route vs eg2-image@768

`eg2-blend:g31b` vs `eg2-image@768` on reciprocal rank over 39 queries: 1 wins, 0 losses, 38 ties; mean RR delta 0.004; two-sided sign test p = 1.0000. Chosen on the same queries: the p-value is optimistic.

All routes vs eg2-image@768 (reciprocal rank, paired):

| route | wins | losses | ties | mean RR delta | sign-test p |
|---|---|---|---|---|---|
| eg2-blend:g31b | 1 | 0 | 38 | 0.004 | 1.0000 |
| eg2-blend:e4b-bf16 | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-blend:e4b-q4 | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-blend:gemini-medium | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-image@256 | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-caption:gemini-medium | 0 | 1 | 38 | -0.002 | 1.0000 |
| eg2-blend:gemini-low | 1 | 1 | 37 | -0.009 | 1.0000 |
| eg2-caption:g31b | 1 | 1 | 37 | -0.009 | 1.0000 |
| eg2-blend:g12b-q4 | 0 | 1 | 38 | -0.013 | 1.0000 |
| eg2-caption:g12b-q4 | 0 | 3 | 36 | -0.045 | 0.2500 |
| eg2-caption:gemini-low | 1 | 3 | 35 | -0.046 | 0.6250 |
| hybrid:gemini-medium | 0 | 5 | 34 | -0.049 | 0.0625 |
| eg2-caption:e4b-bf16 | 1 | 3 | 35 | -0.056 | 0.6250 |
| hybrid:e4b-q4 | 0 | 6 | 33 | -0.057 | 0.0312 |
| hybrid:g12b-q4 | 0 | 6 | 33 | -0.062 | 0.0312 |
| eg2-caption:e4b-q4 | 0 | 3 | 36 | -0.063 | 0.2500 |
| hybrid:gemini-low | 1 | 8 | 30 | -0.073 | 0.0391 |
| hybrid:e4b-bf16 | 0 | 8 | 31 | -0.079 | 0.0078 |
| hybrid:g31b | 1 | 6 | 32 | -0.084 | 0.1250 |
| fts:gemini-medium | 1 | 6 | 32 | -0.093 | 0.1250 |
| fts:gemini-low | 1 | 8 | 30 | -0.133 | 0.0391 |
| fts:g12b-q4 | 0 | 8 | 31 | -0.147 | 0.0078 |
| fts:g31b | 1 | 9 | 29 | -0.158 | 0.0215 |
| fts:e4b-q4 | 0 | 11 | 28 | -0.176 | 0.0010 |
| fts:e4b-bf16 | 0 | 13 | 26 | -0.234 | 0.0002 |
| eg2-blend@w0.3:g31b | 1 | 0 | 38 | 0.004 | 1.0000 |
| eg2-blend@w0.7:g31b | 1 | 0 | 38 | 0.004 | 1.0000 |
| eg2-blend@w0.7:gemini-low | 1 | 0 | 38 | 0.004 | 1.0000 |
| eg2-blend@w0.3:gemini-medium | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-blend@w0.7:e4b-bf16 | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-blend@w0.7:e4b-q4 | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-blend@w0.7:g12b-q4 | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-blend@w0.7:gemini-medium | 0 | 0 | 39 | 0.000 | 1.0000 |
| eg2-blend@w0.3:gemini-low | 1 | 1 | 37 | -0.009 | 1.0000 |
| eg2-blend@w0.3:e4b-bf16 | 1 | 1 | 37 | -0.013 | 1.0000 |
| eg2-blend@w0.3:g12b-q4 | 0 | 1 | 38 | -0.017 | 1.0000 |
| eg2-blend@w0.3:e4b-q4 | 0 | 2 | 37 | -0.040 | 0.5000 |

## Per labeller: whose captions make photos easiest to find

MRR per route family (hit@5 in brackets); mean over the four when all four ran.

| labeller | caption | fts | blend (w=0.5) | hybrid | mean MRR | items w/o caption | w/o caption vector |
|---|---|---|---|---|---|---|---|
| gemini-medium | 0.981 (1.00) | 0.890 (0.97) | 0.983 (1.00) | 0.934 (1.00) | 0.947 | 0 | 0 |
| g31b | 0.974 (1.00) | 0.825 (0.91) | 0.987 (1.00) | 0.899 (0.95) | 0.921 | 0 | 0 |
| gemini-low | 0.936 (0.97) | 0.850 (0.95) | 0.974 (1.00) | 0.910 (1.00) | 0.918 | 1 | 1 |
| g12b-q4 | 0.938 (1.00) | 0.836 (0.95) | 0.970 (1.00) | 0.921 (0.97) | 0.916 | 0 | 0 |
| e4b-q4 | 0.920 (0.95) | 0.807 (0.95) | 0.983 (1.00) | 0.926 (1.00) | 0.909 | 0 | 0 |
| e4b-bf16 | 0.926 (0.95) | 0.749 (0.85) | 0.983 (1.00) | 0.904 (1.00) | 0.891 | 0 | 0 |

## Blend weight sensitivity (MRR) — not tuned on held-out data

Shown only to see how much w matters. w=0.5 is the fixed primary; do not pick w from this table.

| labeller | w=0.3 | w=0.5 | w=0.7 |
|---|---|---|---|
| gemini-medium | 0.983 | 0.983 | 0.983 |
| g31b | 0.987 | 0.987 | 0.987 |
| gemini-low | 0.974 | 0.974 | 0.987 |
| g12b-q4 | 0.966 | 0.970 | 0.983 |
| e4b-q4 | 0.943 | 0.983 | 0.983 |
| e4b-bf16 | 0.970 | 0.983 | 0.983 |

## Settings

- Blend w = 0.5; RRF k = 60; FTS5 tokenizer `porter unicode61`.
- Bootstrap: 1000 resamples, seed 20261010.

## Notes

- Known-item search: each query is an owner note; relevant = the noted item(s) and capture-group siblings, one target per capture group (a DNG+JPG pair is one target). Only those items were judged for the query: every other item is ASSUMED non-relevant without a judgment, so a photo that also fits a generic note counts as a miss. Pool coverage (judged@k: the judged fraction of the top k) is therefore about 1/k; the metrics are known-item metrics, not judged-pool metrics.
- Ties: every metric is its expectation over random orders within blocks of exactly equal score (the unscored tail is one block), so the stored asset_id tie-break decides nothing. mrr_best/mrr_worst bound MRR over tie orders; tie_decided_queries counts queries whose rank of the first relevant item depends on the tie order (common for hybrid RRF and FTS).
- Blend weight w=0.5 is fixed, not tuned. The w=0.3/0.7 sensitivity table is NOT tuned on held-out data and must not be used to pick w from these queries.
- The best caption-based route is chosen on these same queries, so its sign-test p-value vs eg2-image@768 is optimistic (selection bias). n is small: treat all differences as indicative.
- MRR CI: percentile bootstrap over queries, 1000 resamples, seed 20261010.
- Items with no caption: blend uses their image score; caption/FTS routes rank them last.
- Vally's pass = hit@5 under every tie order (score = expected hit@5); its overall score is not the metric.
