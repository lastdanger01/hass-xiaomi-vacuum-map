"""The Xiaomi cloud, used to read the map of the robot.

The cloud of Xiaomi keeps the map of this model as a json file. The
url of the file is signed by the cloud, the answers are encrypted with
RC4. This module does the talking to the cloud and gives the json of
the map back.

The token of the account is used as it is when it is given, so the
account of the integration xiaomi_miot can be used. When there is no
token, the account is logged in with its name and its word.
"""

import base64
import hashlib
import json
import os
import random
import time
from typing import Any

import requests
from Crypto.Cipher import ARC4

# The endpoint that has the map of the newer models of the robot.
MAP_ENDPOINT = "/v2/home/get_interim_file_url_pro"

# The place where the account of Xiaomi is signed in.
ACCOUNT_BASE = "https://account.xiaomi.com"


class XiaomiCloud:
    """A connection to the cloud of Xiaomi."""

    def __init__(self, username: str = "", password: str = "", country: str = "cn",
                 service_token: str = "", ssecurity: str = "", user_id: str = "",
                 device_id: str = "") -> None:
        self._username = username
        self._password = password
        self._country = country
        self._service_token = service_token or None
        self._ssecurity = ssecurity or None
        self._user_id = user_id or None
        self._device_id = device_id or self._generate_device_id()
        self._agent = self._generate_agent()
        self._session = requests.session()
        self._cookies = {
            "locale": "en_GB",
            "timezone": "GMT+02:00",
            "is_daylight": "1",
            "dst_offset": "3600000",
            "channel": "MI_APP_STORE",
        }

    @staticmethod
    def _generate_agent() -> str:
        agent_id = "".join(chr(random.randint(65, 69)) for _ in range(13))
        return (f"Android-7.1.1-1.0.0-ONEPLUS A3010-136-{agent_id} "
                "APP/xiaomi.smarthome APPV/62830")

    @staticmethod
    def _generate_device_id() -> str:
        return "".join(chr(random.randint(97, 122)) for _ in range(6))

    def get_api_url(self) -> str:
        country = "" if self._country == "cn" else f"{self._country}."
        return f"https://{country}api.io.mi.com/app"

    @property
    def logged_in(self) -> bool:
        return bool(self._service_token and self._ssecurity and self._user_id)

    # --- the encryption of the cloud -------------------------------------

    @staticmethod
    def _generate_nonce(millis: int) -> str:
        nonce_bytes = os.urandom(8) + (int(millis / 60000)).to_bytes(4, "big")
        return base64.b64encode(nonce_bytes).decode()

    def _signed_nonce(self, nonce: str) -> str:
        digest = hashlib.sha256(
            base64.b64decode(self._ssecurity) + base64.b64decode(nonce)
        ).digest()
        return base64.b64encode(digest).decode()

    @staticmethod
    def _encrypt_rc4(password: str, payload: str) -> str:
        cipher = ARC4.new(base64.b64decode(password))
        cipher.encrypt(bytes(1024))
        return base64.b64encode(cipher.encrypt(payload.encode())).decode()

    @staticmethod
    def _decrypt_rc4(password: str, payload: str) -> bytes:
        cipher = ARC4.new(base64.b64decode(password))
        cipher.encrypt(bytes(1024))
        return cipher.encrypt(base64.b64decode(payload))

    @staticmethod
    def _enc_signature(url: str, method: str, signed_nonce: str,
                       params: dict[str, str]) -> str:
        parts = [str(method).upper(), url.split("com")[1].replace("/app/", "/")]
        parts.extend(f"{key}={value}" for key, value in params.items())
        parts.append(signed_nonce)
        signature_string = "&".join(parts)
        return base64.b64encode(
            hashlib.sha1(signature_string.encode()).digest()
        ).decode()

    def _enc_params(self, url: str, method: str, signed_nonce: str,
                    nonce: str, params: dict[str, str]) -> dict[str, str]:
        params = dict(params)
        params["rc4_hash__"] = self._enc_signature(url, method, signed_nonce, params)
        for key, value in list(params.items()):
            params[key] = self._encrypt_rc4(signed_nonce, value)
        params.update({
            "signature": self._enc_signature(url, method, signed_nonce, params),
            "ssecurity": self._ssecurity,
            "_nonce": nonce,
        })
        return params

    def _call(self, url: str, params: dict[str, str]) -> Any:
        """Ask the cloud something, the answer comes back encrypted."""
        if not self.logged_in:
            return None
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = dict(self._cookies)
        cookies.update({
            "userId": str(self._user_id),
            "yetAnotherServiceToken": str(self._service_token),
            "serviceToken": str(self._service_token),
        })
        millis = round(time.time() * 1000)
        nonce = self._generate_nonce(millis)
        signed_nonce = self._signed_nonce(nonce)
        fields = self._enc_params(url, "POST", signed_nonce, nonce, params)
        try:
            response = self._session.post(url, headers=headers, cookies=cookies,
                                          params=fields, timeout=15)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            decoded = self._decrypt_rc4(self._signed_nonce(fields["_nonce"]),
                                        response.text)
            return json.loads(decoded)
        except (ValueError, TypeError):
            return None

    # --- the login --------------------------------------------------------

    def login(self) -> bool:
        """Sign in to the cloud of Xiaomi with the account.

        The sign in goes the same way as the integration xiaomi_miot
        does it, so an account that works there works here too. The
        token that is in the storage of Home Assistant is often too
        old to be used, so the sign in is done as well.
        """
        if self._username and self._password:
            try:
                if self._login_service():
                    return True
            except (requests.RequestException, ValueError, KeyError):
                pass
        return self.logged_in

    def _login_service(self) -> bool:
        """The three steps of the sign in of the cloud of Xiaomi."""
        cookies = {"sdkVersion": "3.8.6", "deviceId": self._device_id}

        # The first step gives the values that the second step needs.
        response = self._session.get(
            f"{ACCOUNT_BASE}/pass/serviceLogin",
            params={"sid": "xiaomiio", "_json": "true"},
            headers={"User-Agent": self._agent},
            cookies=cookies,
            timeout=15,
        )
        auth = json.loads(response.text.replace("&&&START&&&", "")) or {}

        # The second step gives the place where the token is handed out.
        post = {
            "user": self._username,
            "hash": hashlib.md5(self._password.encode()).hexdigest().upper(),
            "callback": auth.get("callback") or "",
            "sid": auth.get("sid") or "xiaomiio",
            "qs": auth.get("qs") or "",
            "_sign": auth.get("_sign") or "",
        }
        response = self._session.post(
            f"{ACCOUNT_BASE}/pass/serviceLoginAuth2",
            data=post,
            params={"_json": "true"},
            headers={"User-Agent": self._agent},
            cookies=cookies,
            timeout=15,
        )
        answer = json.loads(response.text.replace("&&&START&&&", "")) or {}
        location = answer.get("location")
        if not location:
            return False

        self._user_id = str(answer.get("userId", ""))
        self._ssecurity = answer.get("ssecurity")

        # The third step hands out the token of the service.
        response = self._session.get(
            location,
            headers={"User-Agent": self._agent,
                     "content-type": "application/x-www-form-urlencoded"},
            cookies=cookies,
            timeout=15,
        )
        service_token = response.cookies.get("serviceToken")
        if not service_token:
            return False
        self._service_token = service_token
        return self.logged_in

    # --- the map ----------------------------------------------------------

    def get_map(self, obj_name: str) -> dict[str, Any] | None:
        """Get the json of the map with the given name."""
        url = self.get_api_url() + MAP_ENDPOINT
        params = {"data": json.dumps({"obj_name": obj_name}, separators=(",", ":"))}
        answer = self._call(url, params)
        result = (answer or {}).get("result") or {}
        file_url = result.get("url")
        if not file_url:
            return None
        try:
            response = self._session.get(file_url, timeout=20)
        except requests.RequestException:
            return None
        if response.status_code != 200 or not response.content:
            return None
        try:
            return json.loads(response.content)
        except ValueError:
            return None

    def get_position(self, did: str) -> list | None:
        """Get the place of the robot, it does not move the robot."""
        url = self.get_api_url() + "/miotspec/prop/get"
        params = {"params": json.dumps([{"did": did, "siid": 7, "piid": 4}])}
        answer = self._call(url, params)
        for entry in answer or []:
            value = entry.get("value")
            if not value:
                continue
            try:
                return json.loads(value).get("position")
            except (ValueError, AttributeError):
                return value
        return None
