# backend/app/agent/memory.py :: embed
def embed(text: str) -> list[list[float]]:
    toks = _tokens(text)
    feats = Counter(toks) + Counter(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
    vec: dict[int, float] = {}
    for f, c in feats.items():
        h = zlib.crc32(f.encode())
        idx = h % DIM
        sign = 1.0 if (h >> 16) & 1 else -1.0
        vec[idx] = vec.get(idx, 0.0) + sign * (1 + math.log(c))
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return [[i, round(v / norm, 5)] for i, v in vec.items()]
