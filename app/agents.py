"""Persistent native-agent credentials and short-lived, single-use pairing codes."""
import hashlib
import hmac
import json
import secrets
import time
from fastapi import HTTPException


class AgentRegistry:
    def __init__(self, settings_path):
        self.settings_path = settings_path
        self.codes = {}
        self.connections = {}

    def records(self):
        path = self.settings_path().parent / "agents.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, ValueError):
            raise HTTPException(500, "Impossible de lire les agents enregistrés.")

    def save(self, records):
        path = self.settings_path().parent / "agents.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            raise HTTPException(500, "Impossible d’enregistrer les agents.")

    def code(self):
        now = time.time()
        self.codes = {code: expiry for code, expiry in self.codes.items() if expiry > now}
        if len(self.codes) >= 100 or len(self.records()) >= 100:
            raise HTTPException(409, "La limite d’association des agents est atteinte.")
        code = secrets.token_hex(8).upper()
        self.codes[code] = now + 300
        return {"code": code, "expires_in": 300}

    def pair(self, code, name):
        code = code.strip().replace("-", "").upper()
        tickets = self.installation_tickets()
        digest = hashlib.sha256(code.encode()).hexdigest()
        if self.codes.get(code, 0) <= time.time() and tickets.get(digest, 0) <= time.time():
            raise HTTPException(401, "Code d’association invalide ou expiré.")
        records = self.records()
        if len(records) >= 100:
            raise HTTPException(409, "La limite de 100 agents est atteinte.")
        token = secrets.token_urlsafe(32)
        record = {"id": secrets.token_hex(16), "name": name.strip() or "PC Windows",
                  "token_hash": hashlib.sha256(token.encode()).hexdigest()}
        if digest in tickets:
            tickets.pop(digest)
            self.save_tickets(tickets)
        self.save(records + [record])
        self.codes.pop(code, None)
        return {"id": record["id"], "token": token}

    def installation_tickets(self):
        path = self.settings_path().parent / "agent-installations.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            raise HTTPException(500, "Impossible de lire les installations des agents.")

    def save_tickets(self, tickets):
        path = self.settings_path().parent / "agent-installations.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(tickets), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            raise HTTPException(500, "Impossible d’enregistrer l’installation de l’agent.")

    def installation_code(self):
        now = time.time()
        tickets = {key: expiry for key, expiry in self.installation_tickets().items() if expiry > now}
        if len(tickets) >= 100 or len(self.records()) >= 100:
            raise HTTPException(409, "La limite d’association des agents est atteinte.")
        code = secrets.token_hex(24).upper()
        tickets[hashlib.sha256(code.encode()).hexdigest()] = now + 86400
        self.save_tickets(tickets)
        return code

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 256:
            return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        return next((record for record in self.records() if hmac.compare_digest(record["token_hash"], digest)), None)

    def snapshot(self):
        return [{"id": record["id"], "name": record["name"],
                 "online": record["id"] in self.connections,
                 "status": self.connections.get(record["id"], {}).get("status", "offline"),
                 "error": self.connections.get(record["id"], {}).get("error", ""),
                 "device": self.connections.get(record["id"], {}).get("device", "")}
                for record in self.records()]
