# Model comparison (measured)

Same model family (Huihui-Qwen3.8-27B-abliterated), three quantizations, same machine (RTX 4090 Laptop 16 GB, 64 GB RAM), same prompts, context 65536, KV cache q8_0, llama.cpp b11620, llama.cpp auto GPU/CPU split. Coding tasks are scored by executing the generated code against hidden tests.

| Quantization | File GB | Load s | VRAM busy MB | Gen tok/s | Prompt tok/s | GPU util % | CPU cores | Tool-call OK | Coding pass | Tokens/task |
|---|---|---|---|---|---|---|---|---|---|---|
| Q4_K_M | 15.41 | 13.6 | 13986 | 7.19 | 583.6 | 30.0 | 20.2 | 6/6 | 7/8 | 1216 |
| IQ4_XS | 13.41 | 13.7 | 14008 | 9.52 | 677.0 | 33.6 | 21.1 | 6/6 | 6/8 | 1193 |
| IQ3_S | 11.13 | 12.2 | 13574 | 35.87 | 1195.4 | 94.2 | 1.0 | 6/6 | 7/8 | 1128 |

## Per-task coding results

| Task | Q4_K_M | IQ4_XS | IQ3_S |
|---|---|---|---|
| is_palindrome | PASS | PASS | PASS |
| merge_intervals | PASS | PASS | PASS |
| roman_to_int | PASS | PASS | PASS |
| flatten | PASS | PASS | PASS |
| LRUCache | PASS | FAIL | PASS |
| parse_csv_line | FAIL | FAIL | FAIL |
| top_k_frequent | PASS | PASS | PASS |
| valid_brackets | PASS | PASS | PASS |
