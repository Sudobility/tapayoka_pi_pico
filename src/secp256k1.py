"""Minimal secp256k1 ECDSA (sign + recover) for MicroPython.

Performance note: scalar multiplication is the hot path — once per sign, twice
per recover/verify. A naive affine implementation inverts (a full 256-bit
modular `pow`) on *every* point addition (~334 inversions per scalar-mult),
which costs multiple seconds on an RP2040. This module instead works in
**Jacobian** coordinates, so exactly ONE modular inverse is needed per
scalar-mult (the final conversion back to affine), and uses a precomputed
**fixed-base comb** for the generator G (used by sign and the u1*G term of
recover), which cuts the generator multiply from ~256 point-doublings to 64.
The signatures produced are identical in form to the affine version (same
random k, same low-s/EIP-2 normalization, same v in {27, 28}).
"""

import os

from .keccak import keccak256

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8

# Identity (point at infinity) in Jacobian coords is any point with Z == 0.
_INF = (0, 0, 0)


def _inv(a, m):
    return pow(a % m, m - 2, m)


# --- affine ops: used only for key/address derivation and one-time table setup
def _add_affine(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if x1 == x2 and y1 == y2:
        m = (3 * x1 * x1) * _inv(2 * y1, P) % P
    else:
        m = (y2 - y1) * _inv((x2 - x1) % P, P) % P
    x3 = (m * m - x1 - x2) % P
    y3 = (m * (x1 - x3) - y1) % P
    return (x3, y3)


# --- Jacobian ops: the hot path. x = X/Z^2, y = Y/Z^3; Z == 0 is infinity.
def _j_double(pt):
    x1, y1, z1 = pt
    if z1 == 0 or y1 == 0:
        return _INF
    a = (x1 * x1) % P
    b = (y1 * y1) % P
    c = (b * b) % P
    d = (2 * (((x1 + b) * (x1 + b)) % P - a - c)) % P
    e = (3 * a) % P
    f = (e * e) % P
    x3 = (f - 2 * d) % P
    y3 = (e * (d - x3) - 8 * c) % P
    z3 = (2 * y1 * z1) % P
    return (x3, y3, z3)


def _j_add(p1, p2):
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    if z1 == 0:
        return p2
    if z2 == 0:
        return p1
    z1z1 = (z1 * z1) % P
    z2z2 = (z2 * z2) % P
    u1 = (x1 * z2z2) % P
    u2 = (x2 * z1z1) % P
    s1 = (y1 * z2 * z2z2) % P
    s2 = (y2 * z1 * z1z1) % P
    if u1 == u2:
        if s1 != s2:
            return _INF
        return _j_double(p1)
    h = (u2 - u1) % P
    i = ((2 * h) * (2 * h)) % P
    j = (h * i) % P
    r = (2 * (s2 - s1)) % P
    v = (u1 * i) % P
    x3 = (r * r - j - 2 * v) % P
    y3 = (r * (v - x3) - 2 * s1 * j) % P
    z3 = (((z1 + z2) * (z1 + z2) - z1z1 - z2z2) * h) % P
    return (x3, y3, z3)


def _j_add_affine(p1, q):
    """Mixed addition: Jacobian p1 + affine q (treated as Z2 = 1). Cheaper
    than full Jacobian add; used against precomputed (affine) comb entries."""
    x1, y1, z1 = p1
    if z1 == 0:
        return (q[0], q[1], 1)
    x2, y2 = q
    z1z1 = (z1 * z1) % P
    u2 = (x2 * z1z1) % P
    s2 = (y2 * z1 * z1z1) % P
    if x1 == u2:
        if y1 == s2:
            return _j_double(p1)
        return _INF
    h = (u2 - x1) % P
    hh = (h * h) % P
    i = (4 * hh) % P
    j = (h * i) % P
    r = (2 * (s2 - y1)) % P
    v = (x1 * i) % P
    x3 = (r * r - j - 2 * v) % P
    y3 = (r * (v - x3) - 2 * y1 * j) % P
    z3 = ((z1 + h) * (z1 + h) - z1z1 - hh) % P
    return (x3, y3, z3)


def _to_affine(pt):
    x, y, z = pt
    if z == 0:
        return None
    zi = _inv(z, P)
    zi2 = (zi * zi) % P
    return ((x * zi2) % P, (y * zi2 % P * zi) % P)


def _mul(k, point):
    """Generic scalar multiply of an arbitrary affine point (Jacobian, one
    final inversion). Used for the u2*R term of recover()."""
    k %= N
    if k == 0 or point is None:
        return None
    r = _INF
    a = (point[0], point[1], 1)
    while k:
        if k & 1:
            r = _j_add(r, a)
        a = _j_double(a)
        k >>= 1
    return _to_affine(r)


# --- fixed-base comb for G -------------------------------------------------
# Split a 256-bit scalar into W=4 blocks of D=64 bits. Precompute every sum of
# {2^(j*D) * G : j in chosen set}; the main loop then runs D=64 iterations of
# one double + one (mixed) add, versus ~256 doubles for a plain binary multiply.
_COMB_W = 4
_COMB_D = 64
_G_COMB = None  # lazily built: index m (1..15) -> affine point, bit j => 2^(j*D)*G


def _g_comb():
    global _G_COMB
    if _G_COMB is not None:
        return _G_COMB
    # base[j] = 2^(j*D) * G, computed by repeated doubling of G in Jacobian.
    base = []
    acc = (GX, GY, 1)
    for j in range(_COMB_W):
        base.append(_to_affine(acc))
        if j != _COMB_W - 1:
            for _ in range(_COMB_D):
                acc = _j_double(acc)
    table = [None] * (1 << _COMB_W)
    for m in range(1, 1 << _COMB_W):
        pt = None
        for j in range(_COMB_W):
            if (m >> j) & 1:
                pt = _add_affine(pt, base[j])
        table[m] = pt
    _G_COMB = table
    return table


def _mul_g(k):
    """Fast fixed-base scalar multiply k*G using the precomputed comb."""
    k %= N
    if k == 0:
        return None
    table = _g_comb()
    r = _INF
    for i in range(_COMB_D - 1, -1, -1):
        r = _j_double(r)
        m = 0
        for j in range(_COMB_W):
            if (k >> (j * _COMB_D + i)) & 1:
                m |= 1 << j
        if m:
            r = _j_add_affine(r, table[m])
    return _to_affine(r)


def privkey_to_pubkey(priv):
    d = int.from_bytes(priv, "big")
    return _mul_g(d)


def _hex(b):
    return "".join("{:02x}".format(x) for x in b)


def pubkey_to_address(x, y):
    pub = x.to_bytes(32, "big") + y.to_bytes(32, "big")
    return "0x" + _hex(keccak256(pub)[-20:])


def sign(msg_hash, priv):
    z = int.from_bytes(msg_hash, "big")
    d = int.from_bytes(priv, "big")
    while True:
        k = int.from_bytes(os.urandom(32), "big") % N
        if k == 0:
            continue
        rp = _mul_g(k)
        r = rp[0] % N
        if r == 0:
            continue
        s = (_inv(k, N) * (z + r * d)) % N
        if s == 0:
            continue
        rec = (rp[1] & 1) | (2 if rp[0] >= N else 0)
        if s > N // 2:
            s = N - s
            rec ^= 1
        return (r, s, 27 + rec)


def recover(msg_hash, r, s, v):
    z = int.from_bytes(msg_hash, "big")
    rec = v - 27
    x = r + (N if (rec & 2) else 0)
    y2 = (pow(x, 3, P) + 7) % P
    y = pow(y2, (P + 1) // 4, P)
    if (y & 1) != (rec & 1):
        y = P - y
    rp = (x, y)
    r_inv = _inv(r, N)
    u1 = (-z * r_inv) % N
    u2 = (s * r_inv) % N
    return _add_affine(_mul_g(u1), _mul(u2, rp))
