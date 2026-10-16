"""Сервер подписки для VPN (Xray, VLESS + Reality)."""

# =====================================================================
# ⚙️ ГЛАВНЫЕ НАСТРОЙКИ ПОДПИСКИ (МЕНЯТЬ ЗДЕСЬ — В САМОМ ВЕРХУ)
# =====================================================================

# 1. Название всей группы подписки (заголовок карточки в приложении)
PROFILE_TITLE = "     ✿❀ρκʜ ɞ ʜυ∂ϱρ᧘αʜ∂αχ❀✿"

# 2. Название первого конфига (обычный, без фильтрации)
CONFIG_1_NAME = "ⲏ.υ.ⲇ.ⲉ.ⲣ.ⲗ.ⲁ.ⲏ.ⲇ.ы"

# 3. Включить второй конфиг с блокировкой рекламы (True — включён, False — выключен)
ENABLE_ADBLOCK_CONFIG = True

# 4. Название второго конфига (с максимальной блокировкой рекламы)
CONFIG_2_ADBLOCK_NAME = "ⲏ.υ.ⲇ.ⲉ.ⲣ.ⲗ.ⲁ.ⲏ.ⲇ.ы ⳝ᥈𐔖ⲕ. ρᥱⲕ᥈ᥲⲙ"

# 5. Лимит трафика для полосы в гигабайтах (0 — скрыть полосу)
TOTAL_GB = 1337

# 6. Дата окончания подписки в формате Unix (4102444799 = 31.12.2099)
EXPIRE_TS = 2165475600

# 7. Как часто приложение само обновляет подписку (в часах)
UPDATE_HOURS = 6

# 8. Дополнительные ссылки (можно оставить пустыми)
SUPPORT_URL = ""
WEB_PAGE_URL = ""

# Если False — всегда берутся настройки из блока выше
USE_SETTINGS_JSON_FILE = False

# =====================================================================
# ДАЛЬШЕ ИДЁТ СИСТЕМНЫЙ КОД СЕРВЕРА (МЕНЯТЬ НЕ НУЖНО)
# =====================================================================

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

SUB_SECRET = os.environ.get("SUB_SECRET", "")
SUB_HOST = os.environ.get("SUB_HOST", "0.0.0.0")
SUB_PORT = int(os.environ.get("SUB_PORT", "8088"))

SERVER_IP = os.environ.get("SERVER_IP", "")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "443"))
VLESS_UUID = os.environ.get("VLESS_UUID", "")
REALITY_PUBLIC_KEY = os.environ.get("REALITY_PUBLIC_KEY", "")
REALITY_SHORT_ID = os.environ.get("REALITY_SHORT_ID", "")
REALITY_SNI = os.environ.get("REALITY_SNI", "dl.google.com")

SSL_CERT = os.environ.get("SSL_CERT", "")
SSL_KEY = os.environ.get("SSL_KEY", "")

USAGE_FILE = os.environ.get("USAGE_FILE", "usage.json")
LOG_FILE = os.environ.get("LOG_FILE", "vpn_sub.log")
SETTINGS_FILE = os.environ.get("SETTINGS_FILE", "settings.json")

DEFAULT_SETTINGS = {
    "profile_title": PROFILE_TITLE,
    "config_name": CONFIG_1_NAME,
    "adblock_config_name": CONFIG_2_ADBLOCK_NAME,
    "enable_adblock": ENABLE_ADBLOCK_CONFIG,
    "total_gb": TOTAL_GB,
    "expire_ts": EXPIRE_TS,
    "update_hours": UPDATE_HOURS,
    "support_url": SUPPORT_URL,
    "web_page_url": WEB_PAGE_URL,
}

log = logging.getLogger("vpn_sub")
USAGE_LOCK = threading.Lock()


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    if not USE_SETTINGS_JSON_FILE:
        return s
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            s.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Не удалось прочитать %s (%s), использую значения из шапки кода", SETTINGS_FILE, e)
    return s


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

# Усиленный список рекламных доменов (включая баннерные сети Google/YouTube и мобильную рекламу)
AD_BLOCK_DOMAINS = [
    "geosite:category-ads-all",
    "geosite:google-ads",
    "domain:doubleclick.net",
    "domain:googleads.g.doubleclick.net",
    "domain:static.doubleclick.net",
    "domain:ad.doubleclick.net",
    "domain:mediavisor.doubleclick.net",
    "domain:googlesyndication.com",
    "domain:pagead2.googlesyndication.com",
    "domain:tpc.googlesyndication.com",
    "domain:googleadservices.com",
    "domain:adservice.google.com",
    "domain:adservice.google.ru",
    "domain:ads.youtube.com",
    "domain:s.youtube.com",
    "domain:youtube.cleverads.vn",
    "domain:google-analytics.com",
    "domain:googletagmanager.com",
    "domain:app-measurement.com",
    "domain:firebase-settings.crashlytics.com",
    "domain:an.yandex.ru",
    "domain:yandexadexchange.net",
    "domain:awaps.yandex.net",
    "domain:ads.vk.com",
    "domain:target.my.com",
    "domain:ad.mail.ru",
    "domain:r.mail.ru",
    "domain:unityads.unity3d.com",
    "domain:ads.prd.ie.internal.unity3d.com",
    "domain:applovin.com",
    "domain:applvn.com",
    "domain:vungle.com",
    "domain:chartboost.com",
    "domain:ironsrc.com",
    "domain:inmobi.com",
    "domain:tapjoy.com",
    "domain:moatads.com",
    "domain:criteo.com",
    "domain:criteo.net",
    "domain:outbrain.com",
    "domain:taboola.com",
    "domain:adfox.ru",
    "domain:adriver.ru",
    "domain:betweendigital.com",
    "domain:buzzoola.com",
    "domain:adjust.com",
    "domain:appsflyer.com",
]


def build_config(remarks, ru_direct):
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
                        "fingerprint": "chrome",
                        "spiderX": "",
                    },
                    "sockopt": {"tcpFastOpen": True, "tcpKeepAliveInterval": 15},
                },
            },
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
    }


def build_legacy_config(remarks):
    """Первый конфиг: проверенный («как раньше»)."""
    cfg = build_config(remarks, True)
    cfg["dns"] = {
        "servers": [
            "https://1.1.1.1/dns-query",
            "https://8.8.8.8/dns-query",
            "https://dns.quad9.net/dns-query",
            "1.1.1.1",
            "8.8.8.8",
        ],
        "queryStrategy": "IPIfNonMatch",
    }
    cfg["routing"]["rules"] = cfg["routing"]["rules"][:4]
    cfg["inbounds"][0]["sniffing"] = {"enabled": True, "destOverride": ["http", "tls", "quic", "fakedns"]}
    cfg["outbounds"][0]["streamSettings"]["realitySettings"]["fingerprint"] = "firefox"
    return cfg


def build_adblock_config(remarks):
    """Второй конфиг: Усиленная блокировка рекламы (AdGuard DNS + перехват порта 53 + блок QUIC UDP 443)."""
    cfg = build_legacy_config(remarks)

    # 1. Настраиваем AdGuard DNS со встроенной блокировкой рекламных хостов
    cfg["dns"] = {
        "hosts": {
            "domain:doubleclick.net": "127.0.0.1",
            "domain:googlesyndication.com": "127.0.0.1",
            "domain:googleadservices.com": "127.0.0.1",
            "domain:adservice.google.com": "127.0.0.1",
            "domain:ads.youtube.com": "127.0.0.1",
            "domain:an.yandex.ru": "127.0.0.1",
            "domain:ads.vk.com": "127.0.0.1",
        },
        "servers": [
            "https://dns.adguard-dns.com/dns-query",
            "94.140.14.14",
            "94.140.15.15",
        ],
        "queryStrategy": "UseIPv4",
    }

    # 2. Добавляем специальный выход dns-out, чтобы все DNS-запросы телефона шли строго через фильтр Xray
    cfg["outbounds"].append({"tag": "dns-out", "protocol": "dns"})

    # 3. Включаем глубокий sniffing (routeOnly=False), чтобы Xray проверял домены внутри трафика
    cfg["inbounds"][0]["sniffing"] = {
        "enabled": True,
        "routeOnly": False,
        "destOverride": ["http", "tls", "quic", "fakedns"],
    }

    # 4. Правила маршрутизации: перехват DNS (порт 53), блок рекламы, блок UDP 443 (QUIC)
    cfg["routing"] = {
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {"port": "53", "outboundTag": "dns-out"},
            {"domain": AD_BLOCK_DOMAINS, "outboundTag": "block"},
            {"network": "udp", "port": "443", "outboundTag": "block"},
        ] + cfg["routing"]["rules"],
    }
    return cfg


def build_subscription(settings):
    configs = [
        build_legacy_config(settings["config_name"]),
    ]
    if settings.get("enable_adblock", True):
        configs.append(build_adblock_config(settings["adblock_config_name"]))
    return configs


def read_tx_bytes():
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
            delta = cur - last if cur >= last else cur
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

        s = load_settings()
        body = json.dumps(build_subscription(s), ensure_ascii=False).encode("utf-8")
        used = update_usage()
        title = "base64:" + base64.b64encode(str(s["profile_title"]).encode("utf-8")).decode()
        total = int(float(s["total_gb"]) * 1024 ** 3)

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Profile-Title", title)
        self.send_header("Profile-Update-Interval", str(s["update_hours"]))
        self.send_header("Subscription-Userinfo", f"upload=0; download={used}; total={total}; expire={int(s['expire_ts'])}")
        if s["support_url"]:
            self.send_header("Support-Url", str(s["support_url"]))
        if s["web_page_url"]:
            self.send_header("Profile-Web-Page-Url", str(s["web_page_url"]))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)
        log.info("Подписка отдана: %s", self.client_address[0])

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head_only=True)

    def log_message(self, fmt, *args):
        pass


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
