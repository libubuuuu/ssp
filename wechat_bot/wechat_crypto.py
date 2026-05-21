"""
WeCom (企业微信) message encryption/decryption.
Implements WXBizMsgCrypt spec without external dependencies.
"""
import hashlib
import base64
import struct
import os
import xml.etree.ElementTree as ET
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


class WxCryptoError(Exception):
    pass


class WxCrypto:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.key = base64.b64decode(encoding_aes_key + "=")  # 43 chars → 32 bytes
        self.corp_id = corp_id

    # ── signature ────────────────────────────────────────────────────────────

    def _sha1(self, *parts) -> str:
        s = "".join(sorted(parts))
        return hashlib.sha1(s.encode("utf-8")).hexdigest()

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """Verify URL callback and return decrypted echostr."""
        expected = self._sha1(self.token, timestamp, nonce, echostr)
        if expected != msg_signature:
            raise WxCryptoError("signature mismatch")
        return self._decrypt(echostr)

    def verify_message(self, msg_signature: str, timestamp: str, nonce: str, msg_encrypt: str) -> bool:
        expected = self._sha1(self.token, timestamp, nonce, msg_encrypt)
        return expected == msg_signature

    # ── decrypt ───────────────────────────────────────────────────────────────

    def _decrypt(self, msg_encrypt: str) -> str:
        ciphertext = base64.b64decode(msg_encrypt)
        iv = self.key[:16]
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=default_backend())
        dec = cipher.decryptor()
        plaintext = dec.update(ciphertext) + dec.finalize()
        # PKCS7 unpad
        pad = plaintext[-1]
        plaintext = plaintext[:-pad]
        # layout: 16-byte random | 4-byte big-endian length | msg | corp_id
        msg_len = struct.unpack(">I", plaintext[16:20])[0]
        return plaintext[20 : 20 + msg_len].decode("utf-8")

    def decrypt_message(self, xml_body: bytes, msg_signature: str, timestamp: str, nonce: str) -> str:
        """Decrypt WeCom message XML body, return plain inner XML string."""
        root = ET.fromstring(xml_body)
        msg_encrypt = root.findtext("Encrypt", "")
        if not msg_encrypt:
            raise WxCryptoError("no Encrypt field")
        if not self.verify_message(msg_signature, timestamp, nonce, msg_encrypt):
            raise WxCryptoError("signature mismatch")
        return self._decrypt(msg_encrypt)
