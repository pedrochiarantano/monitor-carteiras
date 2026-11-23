# -*- coding: utf-8 -*-
"""Criptografia dos arquivos de dados do Monitor de Carteiras.

Formato de cada arquivo .enc: JSON {"v":1,"iv":<base64>,"ct":<base64>},
onde ct = AES-256-GCM(DATA_KEY, iv, gzip(json)). A página decifra com a mesma
chave, que cada usuário recebe "embrulhada" pela própria senha (data/usuarios.json).

DATA_KEY: 32 bytes em base64, guardada no GitHub Secret DATA_KEY.
"""
import base64, gzip, json, os, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

ITER = 310000

def chave():
    k = os.environ.get("DATA_KEY", "").strip()
    if not k:
        sys.exit("DATA_KEY não definida")
    b = base64.b64decode(k)
    if len(b) != 32:
        sys.exit("DATA_KEY deve ter 32 bytes em base64")
    return b

def cifrar(obj, key):
    iv = os.urandom(12)
    pt = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), mtime=0)
    ct = AESGCM(key).encrypt(iv, pt, None)
    return {"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}

def decifrar(env, key):
    pt = AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)
    return json.loads(gzip.decompress(pt).decode("utf-8"))

def ler(caminho, key, padrao=None):
    if not os.path.exists(caminho):
        return padrao
    with open(caminho, encoding="utf-8") as f:
        return decifrar(json.load(f), key)

def gravar(caminho, obj, key):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(cifrar(obj, key), f)

def embrulhar(senha, key, admin=False):
    """Entrada de usuário: a DATA_KEY cifrada com uma chave derivada da senha (PBKDF2-SHA256)."""
    salt = os.urandom(16)
    kek = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(senha.encode("utf-8"))
    iv = os.urandom(12)
    wk = AESGCM(kek).encrypt(iv, key, None)
    return {"salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
            "wk": base64.b64encode(wk).decode(), "admin": admin}
