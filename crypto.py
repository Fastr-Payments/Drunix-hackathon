"""ed25519 helpers. Same scheme the Go chaincode verifies (crypto/ed25519), so proofs are checkable on-chain."""
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

def keypair():
    k = Ed25519PrivateKey.generate()
    return k, k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

def sign(priv, msg: bytes) -> str: return priv.sign(msg).hex()

def verify(pub_hex: str, msg: bytes, sig_hex: str) -> bool:
    try: Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(bytes.fromhex(sig_hex), msg); return True
    except Exception: return False

def canon(intent: dict) -> bytes:
    return json.dumps({k: v for k, v in intent.items() if k != "sig"}, sort_keys=True, separators=(",", ":")).encode()
