from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_PH = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16)


def hash_password(plain: str) -> str:
    return _PH.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _PH.verify(hashed, plain)
    except VerifyMismatchError:
        return False
