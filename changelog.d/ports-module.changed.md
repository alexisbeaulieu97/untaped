lint's `protocol-location` rule takes a `Protocol` in any module named
`ports`, so a plugin laid out flat keeps its ports in `ports.py`;
`application/ports.py` still passes.
