"""Сервер подписки для VPN (Xray, VLESS + Reality).

Отдаёт по секретной ссылке массив JSON-конфигов и заголовки, по которым приложение
рисует название группы, полосу трафика и дату окончания (как у друга на скрине).
Секреты (UUID, ключи) здесь не хранятся: всё берётся из переменных окружения (.env на сервере).
"""
import base64
import hmac
import json
import logging
import os
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

# ==================== НАСТРОЙКИ ====================
SUB_SECRET = os.environ.get("SUB_SECRET", "")  # секретная часть ссылки
SUB_HOST = os.environ.get("SUB_HOST", "0.0.0.0")
SUB_PORT = int(os.environ.get("SUB_PORT", "8088"))

SERVER_IP = os.environ.get("SERVER_IP", "")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "443"))
VLESS_UUID = os.environ.get("VLESS_UUID", "")
REALITY_PUBLIC_KEY = os.environ.get("REALITY_PUBLIC_KEY", "")
REALITY_SHORT_ID = os.environ.get("REALITY_SHORT_ID", "")
REALITY_SNI = os.environ.get("REALITY_SNI", "dl.google.com")
FINGERPRINT = os.environ.get("FINGERPRINT", "chrome")

PROFILE_TITLE = os.environ.get("PROFILE_TITLE", "🚀 Мой VPN | Нидерланды 🚀")
CONFIG_NAME_1 = os.environ.get("CONFIG_NAME_1", "🇳🇱 Нидерланды | РФ напрямую")
CONFIG_NAME_2 = os.environ.get("CONFIG_NAME_2", "🇳🇱 Нидерланды | всё через VPN")

TOTAL_GB = float(os.environ.get("TOTAL_GB", "1000"))  # "лимит" для полосы; 0 — без полосы
EXPIRE_TS = int(os.environ.get("EXPIRE_TS", "4102444799"))  # по умолчанию 31.12.2099
UPDATE_HOURS = int(os.environ.get("UPDATE_HOURS", "6"))  # как часто приложение обновляет подписку
SUPPORT_URL = os.environ.get("SUPPORT_URL", "")
WEB_PAGE_URL = os.environ.get("WEB_PAGE_URL", "")

SSL_CERT = os.environ.get("SSL_CERT", "")  # по желанию: https
SSL_KEY = os.environ.get("SSL_KEY", "")

USAGE_FILE = os.environ.get("USAGE_FILE", "usage.json")
LOG_FILE = os.environ.get("LOG_FILE", "vpn_sub.log")

log = logging.getLogger("vpn_sub")
USAGE_LOCK = threading.Lock()


# ==================== ЛОГИРОВАНИЕ ====================
def setup_logging():
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


# ==================== КОНФИГИ ====================
RU_DIRECT_DOMAINS = [
    "geosite:category-ru", "domain:ru", "domain:su", "domain:xn--p1ai",
    "domain:sberbank.com", "domain:vtb.com", "domain:vk.com", "domain:vk.me",
    "domain:vkuser.net", "domain:userapi.com", "domain:yandex.net", "domain:yandex.com",
    "domain:2gis.com", "domain:ozonusercontent.com", "domain:wbstatic.net",
    "geosite:private", "geosite:apple", "geosite:apple-pki", "geosite:huawei", "geosite:xiaomi",
]
BLOCKED_IN_RU_VIA_PROXY = [
    "domain:theins.ru", "domain:tvrain.ru", "domain:echo.msk.ru", "domain:novayagazeta.ru",
    "domain:moscowtimes.ru", "domain:the-village.ru", "domain:snob.ru", "domain:habr.com",
    "domain:4pda.to", "domain:4pda.ru", "domain:rutracker.org", "domain:meduza.io",
]


def build_config(remarks, ru_direct):
    """Клиентский конфиг Xray. ru_direct=True: российские сайты напрямую, остальное через VPN."""
    if ru_direct:
        dns_servers = [
            {
                "address": "77.88.8.8",
                "domains": [
                    "geosite:category-ru", "domain:ru", "domain:su", "domain:xn--p1ai",
                    "domain:vk.com", "domain:vk.me", "domain:vkuser.net", "domain:userapi.com",
                    "domain:yandex.net", "domain:yandex.com",
                ],
                "expectIPs": ["geoip:ru"],
            },
            "https://1.1.1.1/dns-query",
            "https://8.8.8.8/dns-query",
            "https://dns.quad9.net/dns-query",
            "1.1.1.1",
            "8.8.8.8",
        ]
        rules = [
            {"protocol": ["bittorrent"], "outboundTag": "block"},
            {"domain": BLOCKED_IN_RU_VIA_PROXY, "outboundTag": "proxy"},
            {"domain": RU_DIRECT_DOMAINS, "outboundTag": "direct"},
            {"ip": ["geoip:ru", "geoip:private"], "outboundTag": "direct"},
            {"network": "udp", "port": "443", "outboundTag": "block"},
        ]
    else:
        dns_servers = [
            "https://1.1.1.1/dns-query",
            "https://8.8.8.8/dns-query",
            "https://dns.quad9.net/dns-query",
            "1.1.1.1",
            "8.8.8.8",
        ]
        rules = [
            {"protocol": ["bittorrent"], "outboundTag": "block"},
            {"ip": ["geoip:private"], "outboundTag": "direct"},
            {"network": "udp", "port": "443", "outboundTag": "block"},
        ]

    return {
        "remarks": remarks,
        "ps": remarks,
        "log": {"loglevel": "warning"},
        "dns": {"servers": dns_servers, "queryStrategy": "UseIPv4"},
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules},
        "inbounds": [
            {
                "tag": "socks",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth", "userLevel": 8},
                "sniffing": {"enabled": True, "routeOnly": True, "destOverride": ["http", "tls", "quic"]},
            },
            {"tag": "http", "port": 10809, "protocol": "http", "settings": {"userLevel": 8}},
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": SERVER_IP,
                            "port": SERVER_PORT,
                            "users": [{"id": VLESS_UUID, "encryption": "none", "flow": "xtls-rprx-vision"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": REALITY_SNI,
                        "publicKey": REALITY_PUBLIC_KEY,
                        "shortId": REALITY_SHORT_ID,
                        "fingerprint": FINGERPRINT,
                        "spiderX": "",
                    },
                    "sockopt": {"tcpFastOpen": True, "tcpKeepAliveInterval": 15},
                },
            },
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
    }


def build_subscription():
    return [build_config(CONFIG_NAME_1, True), build_config(CONFIG_NAME_2, False)]


# ==================== УЧЁТ ТРАФИКА (приблизительный) ====================
def read_tx_bytes():
    """Сколько байт сервер отправил через основной сетевой интерфейс с момента загрузки."""
    want = os.environ.get("IFACE", "")
    best = -1
    try:
        with open("/proc/net/dev", encoding="utf-8") as f:
            lines = f.read().splitlines()[2:]
    except OSError:
        return None
    for line in lines:
        name, _, rest = line.partition(":")
        name = name.strip()
        fields = rest.split()
        if len(fields) < 9:
            continue
        tx = int(fields[8])
        if want:
            if name == want:
                return tx
            continue
        if name == "lo" or name.startswith(("docker", "veth", "br-", "virbr")):
            continue
        best = max(best, tx)
    return best if best >= 0 else None


def update_usage():
    """Копит исходящий трафик за календарный месяц (UTC). Возвращает байты."""
    with USAGE_LOCK:
        month = time.strftime("%Y-%m", time.gmtime())
        cur = read_tx_bytes()
        try:
            with open(USAGE_FILE, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
        if cur is None:
            return int(st.get("used", 0))
        if st.get("month") != month or "last_tx" not in st:
            st = {"month": month, "last_tx": cur, "used": 0}
        else:
            last = int(st["last_tx"])
            delta = cur - last if cur >= last else cur  # после перезагрузки счётчик начинается с нуля
            st["used"] = int(st.get("used", 0)) + delta
            st["last_tx"] = cur
        try:
            with open(USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except Exception as e:
            log.warning("Не удалось сохранить %s: %s", USAGE_FILE, e)
        return int(st["used"])


def usage_thread():
    while True:
        try:
            update_usage()
        except Exception:
            log.exception("Ошибка учёта трафика")
        time.sleep(300)


# ==================== HTTP ====================
class Handler(BaseHTTPRequestHandler):
    server_version = "nginx"
    sys_version = ""

    def _serve(self, head_only=False):
        path = self.path.split("?")[0]
        if not hmac.compare_digest(path.encode(), ("/" + SUB_SECRET).encode()):
            log.warning("Запрос с неверной ссылкой от %s", self.client_address[0])
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body = json.dumps(build_subscription(), ensure_ascii=False).encode("utf-8")
        used = update_usage()
        title = "base64:" + base64.b64encode(PROFILE_TITLE.encode("utf-8")).decode()
        total = int(TOTAL_GB * 1024 ** 3)

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Profile-Title", title)
        self.send_header("Profile-Update-Interval", str(UPDATE_HOURS))
        self.send_header("Subscription-Userinfo", f"upload=0; download={used}; total={total}; expire={EXPIRE_TS}")
        if SUPPORT_URL:
            self.send_header("Support-Url", SUPPORT_URL)
        if WEB_PAGE_URL:
            self.send_header("Profile-Web-Page-Url", WEB_PAGE_URL)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)
        log.info("Подписка отдана: %s", self.client_address[0])

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head_only=True)

    def log_message(self, fmt, *args):  # свои логи выше, без секретной ссылки
        pass


# ==================== ТОЧКА ВХОДА ====================
if __name__ == "__main__":
    setup_logging()
    missing = [
        n for n, v in (
            ("SUB_SECRET", SUB_SECRET), ("SERVER_IP", SERVER_IP), ("VLESS_UUID", VLESS_UUID),
            ("REALITY_PUBLIC_KEY", REALITY_PUBLIC_KEY), ("REALITY_SHORT_ID", REALITY_SHORT_ID),
        ) if not v
    ]
    if missing:
        log.error("Не заданы переменные окружения: %s", ", ".join(missing))
        raise SystemExit(1)
    if len(SUB_SECRET) < 16:
        log.error("SUB_SECRET слишком короткий (нужно минимум 16 символов)")
        raise SystemExit(1)

    threading.Thread(target=usage_thread, name="usage", daemon=True).start()
    httpd = ThreadingHTTPServer((SUB_HOST, SUB_PORT), Handler)
    scheme = "http"
    if SSL_CERT and SSL_KEY:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(SSL_CERT, SSL_KEY)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    log.info("Сервер подписки запущен на %s://%s:%d", scheme, SUB_HOST, SUB_PORT)
    httpd.serve_forever()
