"""Operator password hashing and session tokens (CONTRACT §2, §4).

Generate a hash for OPERATOR_PASSWORD_HASH:  python -m chargeops.auth hash
"""

import base64
import getpass
import hashlib
import hmac
import secrets
import sys

SESSION_COOKIE = "chargeops_session"
SESSION_TTL_S = 8 * 3600
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def _scrypt(password, salt, n, r, p):
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * n * r * 2)


def hash_password(password, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P):
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, n, r, p)
    b64 = base64.b64encode
    return f"scrypt${n}${r}${p}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password, encoded):
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        expected = base64.b64decode(expected, validate=True)
        digest = _scrypt(password, base64.b64decode(salt, validate=True), n, r, p)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def new_token():
    return secrets.token_urlsafe(32)


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def main():
    if sys.argv[1:] != ["hash"]:
        sys.exit("usage: python -m chargeops.auth hash")
    print(hash_password(getpass.getpass("Operator password: ")))


if __name__ == "__main__":
    main()
