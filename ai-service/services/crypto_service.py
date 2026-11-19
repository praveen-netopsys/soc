import json
import base64
from config.settings import settings

HAS_FERNET = False
try:
    from cryptography.fernet import Fernet
    HAS_FERNET = True
except ImportError:
    HAS_FERNET = False

class CryptoService:
    def __init__(self):
        if HAS_FERNET:
            try:
                self.fernet = Fernet(settings.EMBEDDING_ENCRYPTION_KEY.encode())
            except Exception:
                self.fernet = None
        else:
            self.fernet = None

    def encrypt_embedding(self, embedding: list[float]) -> str:
        """
        Encrypts a list of floats (embedding) into a secure string.
        """
        try:
            serialized = json.dumps(embedding).encode('utf-8')
            if self.fernet:
                encrypted = self.fernet.encrypt(serialized)
                return encrypted.decode('utf-8')
            return base64.b64encode(serialized).decode('utf-8')
        except Exception as e:
            raise RuntimeError(f"Failed to encrypt face embedding: {str(e)}")

    def decrypt_embedding(self, encrypted_embedding: str) -> list[float]:
        """
        Decrypts an encrypted string back into a list of floats.
        """
        try:
            if self.fernet:
                try:
                    decrypted = self.fernet.decrypt(encrypted_embedding.encode('utf-8'))
                    return json.loads(decrypted.decode('utf-8'))
                except Exception:
                    pass
            dec = base64.b64decode(encrypted_embedding.encode('utf-8'))
            return json.loads(dec.decode('utf-8'))
        except Exception as e:
            raise RuntimeError(f"Failed to decrypt face embedding: {str(e)}")

crypto_service = CryptoService()
