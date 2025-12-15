"""Encryption utilities for sensitive data using Fernet symmetric encryption."""

import os
from functools import lru_cache
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# Load encryption key from environment
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")


@lru_cache
def get_fernet() -> Fernet:
    """Get or create Fernet cipher instance."""
    if not ENCRYPTION_KEY:
        raise ValueError(
            "ENCRYPTION_KEY environment variable is required. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(ENCRYPTION_KEY.encode())


def encrypt_token(plaintext: str) -> str:
    """
    Encrypt a plaintext token using Fernet symmetric encryption.

    Args:
        plaintext: The token to encrypt

    Returns:
        Base64-encoded encrypted token string
    """
    fernet = get_fernet()
    encrypted = fernet.encrypt(plaintext.encode())
    return encrypted.decode()


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt a Fernet-encrypted token.

    Args:
        encrypted_token: Base64-encoded encrypted token

    Returns:
        Decrypted plaintext token

    Raises:
        cryptography.fernet.InvalidToken: If decryption fails
    """
    fernet = get_fernet()
    decrypted = fernet.decrypt(encrypted_token.encode())
    return decrypted.decode()
