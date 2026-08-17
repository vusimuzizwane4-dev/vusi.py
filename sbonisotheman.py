#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CPM1 All-In-One Termux Tool v2.6
================================
- FIX: _save_force šalje previše polja → server odbija
- NOVO: _save_minimal šalje SAMO navedena polja
- Unlock All Cars: minimalni payload (boughtFsos + fcar)
"""

import asyncio
import aiohttp
import base64
import brotli
import hashlib
import json
import re
import struct
import sys
import zlib
from copy import deepcopy
from typing import Any, Dict, List, Optional

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except ImportError:
    print("[!] Install pycryptodome: pip install pycryptodome")
    sys.exit(1)

# ═══════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════

FK       = "AIzaSyAe_aOVT1gSfmHKBrorFvX4fRwN5nODXVA"
LOAD_URL = "https://europe-west1-cp-multiplayer.cloudfunctions.net/GetPlayerRecords3"
SAVE_URL = "https://europe-west1-cp-multiplayer.cloudfunctions.net/SavePlayerRecordsPartially8"
RANK_URL = "https://us-central1-cp-multiplayer.cloudfunctions.net/SetUserRating1"

MAX_MONEY = 50_000_000
MAX_COIN  = 500_000

GAME_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
    "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
    "X-Unity-Version": "2022.3.62f2",
}

# ═══════════════════════════════════════════
#  CRYPTO
# ═══════════════════════════════════════════

def make_xor_key(uid: str) -> bytes:
    chars = list(uid)
    if len(chars) >= 9: chars[1], chars[8] = chars[8], chars[1]
    if len(chars) >= 3: chars.pop(2)
    if len(chars) >= 5: chars.append(chars[4])
    return "".join(chars).encode("utf-8")

def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

def decompress(data: bytes) -> Optional[bytes]:
    for fn in (brotli.decompress, lambda x: zlib.decompress(x, zlib.MAX_WBITS | 16), zlib.decompress):
        try: return fn(data)
        except: pass
    return None

def decrypt_aes(data: bytes, key: bytes) -> Optional[bytes]:
    try:
        cipher = AES.new(key[:16], AES.MODE_CBC, b"\x00" * 16)
        return unpad(cipher.decrypt(data), 16)
    except: return None

def _md5(t): return hashlib.md5(t.encode()).digest()
def _sha1(t): return hashlib.sha1(t.encode()).digest()[:16]

def build_aes_keys(uid, password=None, email=None):
    keys = [_md5("olzhas_carparking")]
    if password: keys += [_md5(password), _sha1(password)]
    if uid:      keys += [_md5(uid), _sha1(uid)]
    if email:    keys.append(_md5(email))
    return keys

# ═══════════════════════════════════════════
#  READER / WRITER
# ═══════════════════════════════════════════

class Reader:
    def __init__(self, data):
        self.buf = data; self.pos = 0

    def has_bytes(self, n): return self.pos + n <= len(self.buf)

    def read_byte(self):
        if not self.has_bytes(1): return 0
        v = self.buf[self.pos]; self.pos += 1; return v

    def read_int(self):
        if not self.has_bytes(4): self.pos = len(self.buf); return 0
        v = struct.unpack_from("<i", self.buf, self.pos)[0]; self.pos += 4; return v

    def read_float(self):
        if not self.has_bytes(4): self.pos = len(self.buf); return 0.0
        v = struct.unpack_from("<f", self.buf, self.pos)[0]; self.pos += 4; return v

    def read_string(self):
        marker = self.read_int()
        if marker in (0, -1): return ""
        length = (-marker) - 1 if marker < -1 else marker
        if marker < -1: self.read_int()
        if length > 1_000_000: length = 1_000_000
        if not self.has_bytes(length): return ""
        text = self.buf[self.pos:self.pos + length].decode("utf-8", errors="replace")
        self.pos += length
        return text.replace("\x00", "").strip()

    def read_list(self, item_fn):
        count = self.read_int()
        if count <= 0 or count > 1_000_000: return []
        result = []
        for _ in range(count):
            if self.pos >= len(self.buf): break
            v = item_fn()
            if v is not None: result.append(v)
        return result

    def read_dict(self):
        count = self.read_int()
        if count <= 0 or count > 1_000_000: return {}
        d = {}
        for _ in range(count):
            if self.pos >= len(self.buf): break
            d[self.read_int()] = self.read_int()
        return d

    def read_equipment(self):
        if self.read_byte() == 0: return None
        return {
            "hair": self.read_list(self.read_int),
            "face": self.read_list(self.read_int),
            "beard": self.read_list(self.read_int),
            "cap": self.read_list(self.read_int),
            "mask": self.read_list(self.read_int),
            "top": self.read_list(self.read_int),
            "gloves": self.read_list(self.read_int),
            "bag": self.read_list(self.read_int),
            "pants": self.read_list(self.read_int),
            "shoes": self.read_list(self.read_int),
            "glasses": self.read_list(self.read_int),
            "SelectedEquipments": self.read_list(self.read_int),
            "Gender": self.read_int(),
        }


def parse_player(buf):
    r = Reader(buf)
    if r.read_byte() == 0: return None
    p = {}
    p["Name"] = r.read_string(); p["money"] = r.read_int()
    p["coin"] = r.read_int(); p["localID"] = r.read_string()
    p["boughtFsos"] = r.read_list(r.read_int)

    def read_friend():
        r.read_byte()
        return {"id": r.read_string(), "Name": r.read_string(), "accountID": r.read_string()}

    p["FriendsID"] = r.read_list(read_friend)
    p["LevelsDoneTime"] = r.read_list(r.read_float)
    p["floats"] = r.read_list(r.read_float)
    p["integers"] = r.read_list(r.read_int)
    p["fcar"] = r.read_list(r.read_int)
    p["favouriteWheels"] = r.read_list(r.read_int)
    p["favouriteVinyls"] = r.read_list(r.read_int)
    p["favouriteEmojis"] = r.read_list(r.read_int)
    p["personEquipmentsMale"] = r.read_equipment()
    p["personEquipmentsFemale"] = r.read_equipment()

    if r.read_byte() == 0:
        p["platesData"] = None
    else:
        def read_vinyl():
            r.read_byte()
            def rv(): return {"x": r.read_float(), "y": r.read_float(), "z": r.read_float()}
            return {"vectors": r.read_list(rv), "v": r.read_list(r.read_string),
                    "floats": r.read_list(r.read_float), "text": r.read_string()}
        def read_plate():
            r.read_byte()
            return {"plateId": r.read_int(), "frontCarId": r.read_int(),
                    "rearCarId": r.read_int(), "vinyls": r.read_list(read_vinyl)}
        p["platesData"] = {"allPlates": r.read_list(read_plate)}

    if r.read_byte() == 0:
        p["carIDnStatus"] = None
    else:
        p["carIDnStatus"] = {
            "carGeneratedIDs": r.read_list(r.read_string),
            "carStatus": r.read_list(r.read_int),
        }

    p["allData"] = r.read_string()
    p["flags"] = r.read_dict()
    p["animations"] = r.read_list(r.read_int)
    p["emojiPacks"] = r.read_list(r.read_int)
    p["wheels"] = r.read_list(r.read_int)
    p["boughtPoliceLights"] = r.read_list(r.read_int)
    p["boughtPoliceSirens"] = r.read_list(r.read_int)
    return p


def try_parse(buf):
    candidates = [buf]
    d1 = decompress(buf)
    if d1:
        candidates.append(d1)
        d2 = decompress(d1)
        if d2: candidates.append(d2)
    for c in candidates:
        if not c: continue
        if len(c) > 0 and c[0] in (17, 23, 24):
            try:
                p = parse_player(c)
                if p and p.get("Name") is not None: return p
            except: pass
        try:
            clean = c[3:] if (len(c) >= 3 and c[0] == 0xef and c[1] == 0xbb) else c
            if clean[0] == 123: return json.loads(clean.decode("utf-8"))
        except: pass
    return None


def decrypt_player_record(base64_text, uid, password=None, email=None):
    try: buf = base64.b64decode(base64_text)
    except: return {"success": False, "message": "Bad base64"}
    if len(buf) < 10: return {"success": False, "message": "Too small"}

    direct = try_parse(buf)
    if direct: return {"success": True, "record": direct}

    if uid:
        try:
            xp = xor_bytes(buf, make_xor_key(uid))
            d  = decompress(xp)
            if d:
                p = try_parse(d)
                if p: return {"success": True, "record": p}
        except: pass

    for key in build_aes_keys(uid or "", password, email):
        plain = decrypt_aes(buf, key)
        if not plain: continue
        p = try_parse(plain)
        if p: return {"success": True, "record": p}

    return {"success": False, "message": "Could not decrypt"}


# ── Writer ────────────────────────────────

class Writer:
    def __init__(self): self._p: List[bytes] = []
    def write_byte(self, v): self._p.append(bytes([v & 0xFF]))
    def write_int(self, v):  self._p.append(struct.pack("<i", int(v or 0)))
    def write_float(self, v): self._p.append(struct.pack("<f", float(v or 0.0)))

    def write_string(self, s):
        if s is None: self._p.append(struct.pack("<i", -1)); return
        s = str(s)
        if s == "": self._p.append(struct.pack("<i", 0)); return
        enc = s.encode("utf-8")
        self._p.append(struct.pack("<ii", -(len(enc)) - 1, len(s)) + enc)

    def write_list(self, lst, fn):
        if lst is None: self._p.append(struct.pack("<i", -1)); return
        self._p.append(struct.pack("<i", len(lst)))
        for item in lst: fn(item)

    def write_equipment(self, data):
        if not data: self.write_byte(0); return
        self.write_byte(13)
        for k in ["hair","face","beard","cap","mask","top","gloves","bag","pants","shoes","glasses","SelectedEquipments"]:
            self.write_list(data.get(k, []), self.write_int)
        self.write_int(data.get("Gender", 0))

    def write_plates(self, data):
        if not data: self.write_byte(0); return
        self.write_byte(1)
        plates = data.get("allPlates", [])
        self._p.append(struct.pack("<i", len(plates)))
        for plate in plates:
            self.write_byte(4)
            self.write_int(plate.get("plateId", 0))
            self.write_int(plate.get("frontCarId", 0))
            self.write_int(plate.get("rearCarId", 0))
            vinyls = plate.get("vinyls", [])
            self._p.append(struct.pack("<i", len(vinyls)))
            for vinyl in vinyls:
                self.write_byte(4)
                vecs = vinyl.get("vectors", [])
                self._p.append(struct.pack("<i", len(vecs)))
                for vec in vecs:
                    self._p.append(struct.pack("<fff", vec.get("x",0), vec.get("y",0), vec.get("z",0)))
                self.write_list(vinyl.get("v", []), self.write_string)
                self.write_list(vinyl.get("floats", []), self.write_float)
                self.write_string(vinyl.get("text", ""))

    def write_car_id_status(self, data):
        if not data: self.write_byte(0); return
        self.write_byte(2)
        self.write_list(data.get("carGeneratedIDs", []), self.write_string)
        self.write_list(data.get("carStatus", []), self.write_int)

    def to_bytes(self): return b"".join(self._p)


FIELD_MAPPING = [
    (1,"localID"),(2,"money"),(3,"Name"),(4,"coin"),(5,"allData"),
    (6,"boughtFsos"),(7,"boughtPoliceLights"),(8,"boughtPoliceSirens"),
    (9,"FriendsID"),(10,"LevelsDoneTime"),(11,"floats"),(12,"integers"),
    (13,"fcar"),(14,"favouriteWheels"),(15,"favouriteVinyls"),
    (16,"favouriteEmojis"),(18,"emojiPacks"),
    (41,"personEquipmentsMale"),(42,"personEquipmentsFemale"),
    (43,"platesData"),(44,"carIDnStatus"),(45,"flags"),
    (46,"animations"),(48,"wheels"),
]

INT_LIST_FIELDS   = {6,7,8,12,13,14,15,16,18,46,48}
FLOAT_LIST_FIELDS = {10,11}
ALWAYS_SEND       = set()


def _field_modified(nv, ov):
    if nv is None and ov is None: return False
    if nv is None or ov is None: return True
    if type(nv) != type(ov): return True
    if isinstance(nv, (dict,list)):
        return json.dumps(nv,sort_keys=True) != json.dumps(ov,sort_keys=True)
    return nv != ov


def serialize_field(fid, value):
    w = Writer()
    if fid in (1,3,5): w.write_string(value); return w.to_bytes()
    if fid in (2,4): w.write_int(value or 0); return w.to_bytes()
    if fid == 9:
        friends = value or []
        w._p.append(struct.pack("<i", len(friends)))
        for f in friends:
            w.write_byte(3)
            w.write_string((f or {}).get("id",""))
            w.write_string((f or {}).get("Name",""))
            w.write_string((f or {}).get("accountID",""))
        return w.to_bytes()
    if fid in INT_LIST_FIELDS: w.write_list(value or [], w.write_int); return w.to_bytes()
    if fid in FLOAT_LIST_FIELDS: w.write_list(value or [], w.write_float); return w.to_bytes()
    if fid in (41,42): w.write_equipment(value); return w.to_bytes()
    if fid == 43: w.write_plates(value); return w.to_bytes()
    if fid == 44: w.write_car_id_status(value); return w.to_bytes()
    if fid == 45:
        flags = value or {}
        w._p.append(struct.pack("<i", len(flags)))
        for k, v in flags.items():
            w.write_int(int(k)); w.write_int(int(v))
        return w.to_bytes()
    return None


# ═══════════════════════════════════════════
#  v2.6 FIX: build_payload sada podržava fields_filter
# ═══════════════════════════════════════════

def build_payload(record, uid, original=None, fields_filter=None):
    fields = []
    for fid, key in FIELD_MAPPING:
        # v2.6: Ako je fields_filter postavljen, preskoči polja koja nisu u listi
        if fields_filter is not None and key not in fields_filter:
            continue
            
        value = record.get(key)
        if value is None: continue
        if key in ALWAYS_SEND:
            should = isinstance(value, str) and len(value) > 0
        elif original is not None:
            should = _field_modified(value, original.get(key))
        else:
            should = True
        if not should: continue
        raw = serialize_field(fid, value)
        if raw is not None: fields.append((fid, raw))

    parts = [struct.pack("<i", len(fields))]
    for fid, raw in fields:
        parts.append(struct.pack("<hi", fid, len(raw)))
        parts.append(raw)
    combined   = b"".join(parts)
    compressed = brotli.compress(combined)
    encrypted  = xor_bytes(compressed, make_xor_key(uid))
    return base64.b64encode(encrypted).decode("ascii")


# ═══════════════════════════════════════════
#  CPM1 CLIENT
# ═══════════════════════════════════════════

class CPM1Client:
    def __init__(self):
        self.auth_token: Optional[str] = None
        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.firebase_uid: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.record: Dict[str, Any] = {}
        self.original_record: Dict[str, Any] = {}

    async def _post(self, url, payload, headers=None):
        h = {k:v for k,v in (GAME_HEADERS if headers is None else {**GAME_HEADERS, **headers}).items() if k.lower() != "host"}
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as s:
                async with s.post(url, json=payload, headers=h) as r:
                    text = await r.text()
                    try: return json.loads(text)
                    except: return {"raw": text, "status": r.status}
        except Exception as e:
            print(f"[HTTP Error] {e}"); return None

    async def login(self, email, password):
        self.email = email; self.password = password
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FK}"
        p = {"email":email,"password":password,"returnSecureToken":True,"clientType":"CLIENT_TYPE_ANDROID"}
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as s:
                async with s.post(url, json=p, headers=GAME_HEADERS) as resp:
                    r = await resp.json(content_type=None)
        except Exception as e:
            return {"ok":False,"message":f"NETWORK_ERROR: {e}"}

        if "idToken" in r:
            self.auth_token = r["idToken"]
            self.refresh_token = r.get("refreshToken", "")
            self.firebase_uid = r.get("localId", "")
            return {"ok":True}
        err = str(r.get("error",{}).get("message","")).upper()
        for k in ["EMAIL_NOT_FOUND","INVALID_PASSWORD","INVALID_LOGIN_CREDENTIALS","TOO_MANY_ATTEMPTS","USER_DISABLED","INVALID_EMAIL"]:
            if k in err: return {"ok":False,"message":k}
        return {"ok":False,"message":err}

    async def _refresh(self):
        if not self.refresh_token:
            if self.email and self.password:
                return (await self.login(self.email, self.password)).get("ok", False)
            return False
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as s:
                async with s.post(f"https://securetoken.googleapis.com/v1/token?key={FK}",
                    json={"grant_type":"refresh_token","refresh_token":self.refresh_token},
                    headers={"Content-Type":"application/json"}) as resp:
                    r = await resp.json(content_type=None)
                    if r and r.get("id_token"):
                        self.auth_token = r["id_token"]
                        self.refresh_token = r.get("refresh_token", self.refresh_token)
                        return True
        except: pass
        if self.email and self.password:
            return (await self.login(self.email, self.password)).get("ok", False)
        return False

    async def get_auth(self):
        if not self.auth_token:
            ok = await self._refresh()
            if not ok: return False,"NO_TOKEN",""
        return True,"OK",self.auth_token

    async def load(self, force=False):
        ok,msg,auth = await self.get_auth()
        if not ok: return False
        try:
            r = await self._post(LOAD_URL, {"data":None})
            if not r or not r.get("result"): return False
            dec = decrypt_player_record(r["result"], self.firebase_uid or "", self.password, self.email)
            if dec.get("success") and dec.get("record"):
                self.original_record = deepcopy(dec["record"])
                self.record = dec["record"]
                return True
            return False
        except Exception as e:
            print(f"[Load Error] {e}"); return False

    def _ok(self, v):
        if v in (1,True): return True
        if v in (0,False): return False
        if isinstance(v,str):
            t=v.strip()
            if t=="1": return True
            if t=="0": return False
            try: return self._ok(json.loads(t))
            except: return False
        if isinstance(v,dict):
            for k in ("result","ok","success"):
                if k in v: return self._ok(v[k])
        return False

    # v2.6: _send sada podržava fields_filter
    async def _send(self, record, original=None, fields_filter=None):
        if not self.firebase_uid: return False,"NO_UID"
        try:
            payload = build_payload(record, self.firebase_uid, original, fields_filter)
            r = await self._post(SAVE_URL,
                {"data":{"data":payload,"deviceId":self.firebase_uid[:8]}},
                {**GAME_HEADERS,"Authorization":f"Bearer {self.auth_token}","Connection":"Keep-Alive",
                 "User-Agent":"Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SD1A.210817.036)"})
            if r and self._ok(r): return True,"OK"
            return False,f"SAVE_FAILED: {str(r)[:100]}"
        except Exception as e: return False,str(e)

    async def _save(self, data):
        ok,msg,auth = await self.get_auth()
        if not ok: return {"ok":False,"message":msg}
        ok2,msg2 = await self._send(data, self.original_record)
        if ok2:
            self.original_record = deepcopy(data)
            return {"ok":True}
        return {"ok":False,"message":msg2}

    # FORSIRANI SAVE - šalje SVE polja bez obzira na original
    async def _save_force(self, data):
        ok,msg,auth = await self.get_auth()
        if not ok: return {"ok":False,"message":msg}
        ok2,msg2 = await self._send(data, None)  # None = pošalji SVE polja
        if ok2:
            self.original_record = deepcopy(data)
            return {"ok":True}
        return {"ok":False,"message":msg2}

    # ═══════════════════════════════════════════
    #  v2.6 NOVO: _save_minimal - šalje SAMO navedena polja
    # ═══════════════════════════════════════════
    async def _save_minimal(self, data, fields_filter):
        """
        Šalje SAMO polja navedena u fields_filter listi.
        Ignorira original_record - ne uspoređuje se ništa.
        """
        ok, msg, auth = await self.get_auth()
        if not ok: return {"ok": False, "message": msg}
        
        # Izgradi minimalni record SAMO s traženim poljima
        minimal = {k: deepcopy(data[k]) for k in fields_filter if k in data and data[k] is not None}
        
        if not minimal:
            return {"ok": False, "message": "No fields to send"}
        
        ok2, msg2 = await self._send(minimal, None, fields_filter=fields_filter)
        if ok2:
            # Ažuriraj original samo za poslana polja
            for k in minimal:
                self.original_record[k] = deepcopy(minimal[k])
            return {"ok": True}
        return {"ok": False, "message": msg2}

    async def _modify(self, mods):
        await self.load()
        d = deepcopy(self.record)
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data. Try Refresh first."}
        for k,v in mods.items():
            if k=="money": v=min(v,MAX_MONEY)
            if k=="coin":  v=min(v,MAX_COIN)
            d[k]=v
        return await self._save(d)

    async def _set_floats(self, indices_values):
        await self.load()
        d = deepcopy(self.record)
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data."}
        fl = d.get("floats",[])
        max_idx = max(idx for idx,_ in indices_values)
        while len(fl) <= max_idx: fl.append(0.0)
        for idx,val in indices_values: fl[idx]=float(val)
        d["floats"]=fl
        return await self._save(d)

    async def _set_integers(self, indices_values):
        await self.load()
        d = deepcopy(self.record)
        if not d or not d.get("Name"):
            return {"ok":False,"message":"Could not load account data."}
        it = d.get("integers",[])
        max_idx = max(idx for idx,_ in indices_values)
        while len(it) <= max_idx: it.append(0)
        for idx,val in indices_values: it[idx]=int(val)
        d["integers"]=it
        return await self._save(d)

    # ── Operations ────────────────────────
    async def set_money(self, amount):  return await self._modify({"money": min(amount, MAX_MONEY)})
    async def set_coin(self, amount):   return await self._modify({"coin": min(amount, MAX_COIN)})
    async def set_name(self, name):     return await self._modify({"Name": name})
    async def set_player_id(self, pid): return await self._modify({"localID": pid.upper()})
    async def set_wins(self, amount):   return await self._set_floats([(8, float(amount))])
    async def set_loses(self, amount):  return await self._set_floats([(9, float(amount))])
    async def unlock_w16(self):         return await self._set_floats([(32, 1.0)])
    async def unlock_horns(self):       return await self._set_floats([(27,1.0),(28,1.0),(29,1.0),(30,1.0),(31,1.0)])
    async def disable_damage(self):     return await self._set_floats([(34, 1.0)])
    async def unlimited_fuel(self):     return await self._set_floats([(3, 1.0)])
    async def unlock_smoke(self):       return await self._set_floats([(33, 1.0)])

    async def unlock_animations(self):
        await self.load()
        d = deepcopy(self.record)
        if not d or not d.get("Name"): return {"ok":False,"message":"Could not load account data."}
        d["animations"] = list(set(d.get("animations",[]) + list(range(301))))
        return await self._save(d)

    async def unlock_wheels(self):
        await self.load()
        d = deepcopy(self.record)
        if not d or not d.get("Name"): return {"ok":False,"message":"Could not load account data."}
        d["wheels"] = list(set(d.get("wheels",[]) + list(range(73,221))))
        it = d.get("integers",[])
        while len(it) < 113: it.append(0)
        for i in [0,1,2,3,4,5,110,111,112]: it[i]=1
        d["integers"]=it
        return await self._save(d)

    async def unlock_houses(self):      return await self._set_integers([(8,1),(110,1),(111,1),(112,1)])

    async def complete_levels(self):
        lvl = [0] + [120 if i==43 else 1 for i in range(1,201)]
        return await self._modify({"LevelsDoneTime": lvl})

    # ═══════════════════════════════════════════
    #  v2.6 FIX: unlock_all_cars - minimalni payload
    # ═══════════════════════════════════════════
    async def unlock_all_cars(self, allData_empty=False):
        """
        v2.6: Šalje SAMO boughtFsos i fcar (minimalni payload).
        Ako allData_empty=True, šalje se i prazan allData string.
        """
        await self.load()
        d = deepcopy(self.record)
        if not d or not d.get("Name"): 
            return {"ok":False,"message":"Could not load account data."}

        NO_CAR = {16,25,26,33,34,36,38,46,50,52,56,63,64,67,68,69,71,72,73,
                  75,78,79,80,83,84,90,91,92,93,94,95,96,97,98,263,265,266,267,268}
        VALID_IDS = sorted([i for i in range(0, 274) if i not in NO_CAR])

        # Postavi OBA polja za auta
        current_bought = set(d.get("boughtFsos", []))
        current_bought.update(VALID_IDS)
        d["boughtFsos"] = sorted(current_bought)

        current_fcar = set(d.get("fcar", []))
        current_fcar.update(VALID_IDS)
        d["fcar"] = sorted(current_fcar)

        # Pripremi listu polja za slanje
        fields_to_send = ['boughtFsos', 'fcar']
        
        # NE DIRAJ allData - po defaultu ga ne šaljemo
        print(f"    [DEBUG] boughtFsos: {len(d['boughtFsos'])}")
        print(f"    [DEBUG] fcar: {len(d['fcar'])}")
        print(f"    [DEBUG] allData: {repr(d['allData'][:20]) if d.get('allData') else 'None'} (NE DIRAM)")

        # Ako je uključena opcija, pošalji i prazan allData
        if allData_empty:
            d['allData'] = ''
            fields_to_send.append('allData')
            print(f"    [DEBUG] allData postavljen na PRAZAN string")

        # KORISTI _save_minimal da se pošalju SAMO navedena polja
        result = await self._save_minimal(d, fields_to_send)
        
        if result.get("ok"):
            print(f"    [DEBUG] ✅ Save uspješan! ZATVORI IGRICU POTPUNO pa je ponovo otvori.")
        else:
            print(f"    [DEBUG] ❌ Save failed: {result.get('message')}")
        return result

    async def set_rank(self):
        await self.load()
        ok,msg,auth = await self.get_auth()
        if not ok: return {"ok":False,"message":msg}
        rd = {"RatingData":{"time":1e22,"cars":1e16,"car_fix":1e13,"car_collided":1e12,
            "car_exchange":1e13,"car_trade":1e13,"car_wash":1e13,"slicer_cut":1e13,
            "drift_max":1e14,"drift":1e14,"cargo":1e5,"delivery":1e5,"race_win":3e20,
            "taxi":1e10,"levels":10000990000,"gifts":1e9,"fuel":1e10,"offroad":1e10,
            "speed_banner":1e9,"reactions":1e17,"run":1e9,"real_estate":1e9,
            "t_distance":1e10,"treasure":1e10,"block_post":1e10,"push_ups":1e12,
            "burnt_tire":1e10,"passanger_distance":1e8}}
        r = await self._post(RANK_URL,{"data":json.dumps(rd)},{**GAME_HEADERS,"Authorization":f"Bearer {auth}"})
        if r and self._ok(r): return {"ok":True}
        return {"ok":False,"message":"RANK_FAILED"}

    async def fix_account(self):
        await self.load()
        d = deepcopy(self.record)
        if not d or not d.get("Name"): return {"ok":False,"message":"Could not load account data."}
        bugs=0
        fl = (d.get("floats",[]))[:54]
        while len(fl)<54: fl.append(0.0)
        fixed_fl=[]
        for v in fl:
            if v in (1,1.0): fixed_fl.append(1.0)
            elif isinstance(v,(int,float)) and v>1: bugs+=1; fixed_fl.append(0.0)
            else: fixed_fl.append(float(v) if v else 0.0)
        it = (d.get("integers",[]))[:120]
        while len(it)<120: it.append(0)
        fixed_it=[]
        for v in it:
            if v==1: fixed_it.append(1)
            elif isinstance(v,(int,float)) and v>1: bugs+=1; fixed_it.append(0)
            else: fixed_it.append(int(v) if v else 0)
        d["floats"]=fixed_fl; d["integers"]=fixed_it
        result = await self._save(d)
        return {"ok":True,"bugs_fixed":bugs} if result.get("ok") else {"ok":False,"message":"FIX_FAILED"}

    async def unlock_all(self):
        ops = [
            ("Money ($50M)", lambda: self.set_money(50_000_000)),
            ("Coins (500K)", lambda: self.set_coin(500_000)),
            ("W16 Engine", self.unlock_w16),
            ("Horns", self.unlock_horns),
            ("No Damage", self.disable_damage),
            ("Unlimited Fuel", self.unlimited_fuel),
            ("Smoke", self.unlock_smoke),
            ("Animations", self.unlock_animations),
            ("Wheels", self.unlock_wheels),
            ("Houses", self.unlock_houses),
            ("All Levels", self.complete_levels),
            ("All Cars", self.unlock_all_cars),
            ("Max Rank", self.set_rank),
        ]
        results = []
        for name, fn in ops:
            try:
                r = await fn()
                results.append((name, r.get("ok", False)))
            except Exception as e:
                results.append((name, False))
            await asyncio.sleep(0.4)
        return results


# ═══════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════

def clear():
    print("\033[2J\033[H", end="")

def banner():
    print(r"""
   ██████╗██████╗ ███╗   ███╗    ████████╗ ██████╗  ██████╗ ██╗     
  ██╔════╝██╔══██╗████╗ ████║    ╚══██╔══╝██╔═══██╗██╔═══██╗██║     
  ██║     ██████╔╝██╔████╔██║       ██║   ██║   ██║██║   ██║██║     
  ██║     ██╔═══╝ ██║╚██╔╝██║       ██║   ██║   ██║██║   ██║██║     
  ╚██████╗██║     ██║ ╚═╝ ██║       ██║   ╚██████╔╝╚██████╔╝███████╗
   ╚═════╝╚═╝     ╚═╝     ╚═╝       ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝
                    CPM1 All-In-One Tool v2.6
""")

def fmt(n): return f"{int(n):,}"

def print_result(ok, title, detail=""):
    icon = "✅" if ok else "❌"
    print(f"\n{'='*40}")
    print(f"  {icon}  {title}")
    print(f"{'='*40}")
    if detail: print(f"  {detail}")

async def main():
    clear(); banner()
    client = CPM1Client()

    email = input("[?] CPM Email: ").strip()
    password = input("[?] Password:  ").strip()
    print("\n[+] Logging in...")
    r = await client.login(email, password)
    if not r.get("ok"):
        print(f"[!] Login failed: {r.get('message', 'UNKNOWN')}")
        return
    print(f"[+] Logged in! UID: {client.firebase_uid}")

    print("[+] Loading account data...")
    if not await client.load(force=True):
        print("[!] Failed to load account data.")
        return

    rec = client.record
    print(f"\n{'='*40}")
    print("  📊 ACCOUNT LOADED")
    print(f"{'='*40}")
    print(f"  👤 Name:  {rec.get('Name', 'Unknown')}")
    print(f"  💰 Money: ${fmt(rec.get('money', 0))}")
    print(f"  🪙 Coins: {fmt(rec.get('coin', 0))}")
    print(f"  🆔 ID:    {rec.get('localID', '—')}")
    print(f"  🚗 Cars:  {len(rec.get('fcar', []))}")
    print(f"  🛞 Wheels:{len(rec.get('wheels', []))}")
    print(f"  🎭 Anims: {len(rec.get('animations', []))}")
    print(f"{'='*40}")

    while True:
        print("""
┌────────────────────────────────────────┐
│           🎮 MAIN MENU                 │
├────────────────────────────────────────┤
│  [1]  💰 Set Money ($50M)              │
│  [2]  🪙 Set Coins (500K)              │
│  [3]  🚗 Unlock All Cars               │
│  [4]  🛞 Unlock Wheels                │
│  [5]  🎭 Unlock Animations             │
│  [6]  🚗 W16 Engine                    │
│  [7]  🔊 Horns                         │
│  [8]  🛡 No Damage                     │
│  [9]  ⛽ Unlimited Fuel                 │
│  [10] 💨 Smoke                         │
│  [11] 🏠 Houses                        │
│  [12] 🎮 Complete All Levels           │
│  [13] 🏅 Max Rank                      │
│  [14] ✏️  Change Name                   │
│  [15] 🆔 Change Player ID              │
│  [16] 🏆 Set Wins                      │
│  [17] 😞 Set Loses                     │
│  [18] 🔧 Fix Account Bugs              │
│  [19] 🚀 ★ UNLOCK EVERYTHING ★        │
│  [20] 🔄 Refresh Account Data           │
│  [30] 🚗 Unlock Cars + Clear allData   │
│  [0]  🚪 Exit                          │
└────────────────────────────────────────┘""")
        choice = input("> ").strip()

        if choice == "0":
            print("[+] Goodbye!"); break

        elif choice == "1":
            print("[+] Setting money...")
            r = await client.set_money(50_000_000)
            print_result(r.get("ok"), "MONEY SET", f"💰 ${fmt(50_000_000)}")

        elif choice == "2":
            print("[+] Setting coins...")
            r = await client.set_coin(500_000)
            print_result(r.get("ok"), "COINS SET", f"🪙 {fmt(500_000)} coins")

        elif choice == "3":
            print("[+] Unlocking all cars (minimal payload)...")
            r = await client.unlock_all_cars()
            print_result(r.get("ok"), "ALL CARS UNLOCKED")

        elif choice == "30":
            print("[+] Unlocking all cars + clearing allData...")
            r = await client.unlock_all_cars(allData_empty=True)
            print_result(r.get("ok"), "ALL CARS + allData CLEARED")

        elif choice == "4":
            print("[+] Unlocking wheels...")
            r = await client.unlock_wheels()
            print_result(r.get("ok"), "WHEELS UNLOCKED")

        elif choice == "5":
            print("[+] Unlocking animations...")
            r = await client.unlock_animations()
            print_result(r.get("ok"), "ANIMATIONS UNLOCKED")

        elif choice == "6":
            print("[+] Unlocking W16...")
            r = await client.unlock_w16()
            print_result(r.get("ok"), "W16 UNLOCKED")

        elif choice == "7":
            print("[+] Unlocking horns...")
            r = await client.unlock_horns()
            print_result(r.get("ok"), "HORNS UNLOCKED")

        elif choice == "8":
            print("[+] Disabling damage...")
            r = await client.disable_damage()
            print_result(r.get("ok"), "NO DAMAGE ENABLED")

        elif choice == "9":
            print("[+] Enabling unlimited fuel...")
            r = await client.unlimited_fuel()
            print_result(r.get("ok"), "UNLIMITED FUEL ENABLED")

        elif choice == "10":
            print("[+] Unlocking smoke...")
            r = await client.unlock_smoke()
            print_result(r.get("ok"), "SMOKE UNLOCKED")

        elif choice == "11":
            print("[+] Unlocking houses...")
            r = await client.unlock_houses()
            print_result(r.get("ok"), "HOUSES UNLOCKED")

        elif choice == "12":
            print("[+] Completing all levels...")
            r = await client.complete_levels()
            print_result(r.get("ok"), "ALL LEVELS COMPLETED")

        elif choice == "13":
            print("[+] Setting max rank...")
            r = await client.set_rank()
            print_result(r.get("ok"), "MAX RANK SET")

        elif choice == "14":
            name = input("[?] New name: ").strip()
            if name:
                print("[+] Changing name...")
                r = await client.set_name(name)
                print_result(r.get("ok"), "NAME CHANGED", f"✏️  {name}")

        elif choice == "15":
            pid = input("[?] New Player ID: ").strip()
            if pid:
                print("[+] Changing ID...")
                r = await client.set_player_id(pid)
                print_result(r.get("ok"), "ID CHANGED", f"🆔 {pid.upper()}")

        elif choice == "16":
            try:
                wins = int(input("[?] Wins: ").strip())
                print("[+] Setting wins...")
                r = await client.set_wins(wins)
                print_result(r.get("ok"), "WINS SET", f"🏆 {fmt(wins)}")
            except: print("[!] Invalid number")

        elif choice == "17":
            try:
                loses = int(input("[?] Loses: ").strip())
                print("[+] Setting loses...")
                r = await client.set_loses(loses)
                print_result(r.get("ok"), "LOSES SET", f"😞 {fmt(loses)}")
            except: print("[!] Invalid number")

        elif choice == "18":
            print("[+] Fixing account...")
            r = await client.fix_account()
            if r.get("ok"):
                print_result(True, "ACCOUNT FIXED", f"🔧 {r.get('bugs_fixed', 0)} bugs fixed")
            else:
                print_result(False, "FIX FAILED", r.get("message", ""))

        elif choice == "19":
            print("\n[🚀] UNLOCKING EVERYTHING...")
            print("    This may take a moment...\n")
            results = await client.unlock_all()
            print(f"\n{'='*40}")
            print("  🎉 UNLOCK ALL COMPLETE")
            print(f"{'='*40}")
            ok_count = sum(1 for _, ok in results if ok)
            for name, ok in results:
                print(f"  {'✅' if ok else '❌'} {name}")
            print(f"\n  Total: {ok_count}/{len(results)} successful")

        elif choice == "20":
            print("[+] Refreshing...")
            if await client.load(force=True):
                rec = client.record
                print(f"\n{'='*40}")
                print("  📊 ACCOUNT REFRESHED")
                print(f"{'='*40}")
                print(f"  👤 {rec.get('Name')}")
                print(f"  💰 ${fmt(rec.get('money', 0))}")
                print(f"  🪙 {fmt(rec.get('coin', 0))}")
                print(f"  🚗 {len(rec.get('fcar', []))} cars")
                print(f"{'='*40}")
            else:
                print("[!] Refresh failed")

        else:
            print("[!] Invalid choice")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted")
    except Exception as e:
        print(f"[!] Fatal: {e}")
