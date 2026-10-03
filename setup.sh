#!/bin/bash
# Установка ботов, автообновления с GitHub и API мониторинга
# на чистый сервер Ubuntu 22.04 / 24.04. Запускать от root.

REPO="Oleg-Brysin2010/Under-Repository"
BASE="https://raw.githubusercontent.com/$REPO/main"

if [ "$(id -u)" != "0" ]; then
  echo "Запусти от root"
  exit 1
fi

echo "== 1/6 Проверка доступа к Telegram =="
CODE=$(curl -m 8 -s -o /dev/null -w "%{http_code}" https://api.telegram.org)
echo "Ответ Telegram: $CODE"
if [ -z "$CODE" ] || [ "$CODE" = "000" ]; then
  echo "Telegram с этого сервера не открывается. Установка остановлена."
  exit 1
fi

echo "== 2/6 Установка пакетов =="
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
apt-get update -y
apt-get install -y python3 curl openssl

echo "== 3/6 Токены ботов =="
read -r -p "Токен первого бота (GitHub-бот): " TOKEN1 </dev/tty
read -r -p "Токен второго бота (Enter - пропустить): " TOKEN2 </dev/tty
if [ -z "$TOKEN1" ]; then
  echo "Токен первого бота обязателен."
  exit 1
fi

echo "== 4/6 Файлы ботов =="
mkdir -p /root/bot /root/bot2 /root/stats

curl -fsSL "$BASE/bot.py" -o /root/bot/bot.py || { echo "Не скачался bot.py из репозитория"; exit 1; }
printf 'BOT_TOKEN=%s\nPYTHONUNBUFFERED=1\n' "$TOKEN1" > /root/bot/.env
chmod 600 /root/bot/.env

if [ -n "$TOKEN2" ]; then
  printf 'BOT_TOKEN=%s\nPYTHONUNBUFFERED=1\n' "$TOKEN2" > /root/bot2/.env
  chmod 600 /root/bot2/.env
  curl -fsSL "$BASE/bot2.py" -o /root/bot2/bot2.py || echo "bot2.py в репозитории пока нет, подтянется автообновлением"
fi

make_service() {
cat > /etc/systemd/system/$1.service << EOF
[Unit]
Description=$1
After=network.target

[Service]
WorkingDirectory=$2
EnvironmentFile=$2/.env
ExecStart=/usr/bin/python3 $2/$3
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

make_service bot /root/bot bot.py
make_service bot2 /root/bot2 bot2.py

echo "== 5/6 Мониторинг сервера (API для приложения) =="
if [ ! -f /root/stats/.env ]; then
  echo "STATS_KEY=$(openssl rand -hex 12)" > /root/stats/.env
  chmod 600 /root/stats/.env
fi

cat > /root/stats/stats.py << 'PYEOF'
import hmac, json, os, socket, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

KEY = os.environ.get("STATS_KEY", "")
PORT = int(os.environ.get("STATS_PORT", "8080"))
SERVICES = ["bot", "bot2", "update-bot.timer"]
STATE = {"cpu": 0.0, "cores": []}


def read_stat():
    res = []
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("cpu"):
                p = line.split()
                v = list(map(int, p[1:9]))
                res.append((sum(v), v[3] + v[4]))
    return res


def cpu_thread():
    prev = read_stat()
    while True:
        time.sleep(1)
        cur = read_stat()
        perc = []
        for (t1, i1), (t2, i2) in zip(prev, cur):
            dt, di = t2 - t1, i2 - i1
            perc.append(round(100.0 * (dt - di) / dt, 1) if dt > 0 else 0.0)
        if perc:
            STATE["cpu"], STATE["cores"] = perc[0], perc[1:]
        prev = cur


def cpu_name():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def meminfo():
    d = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            d[k] = int(v.split()[0]) * 1024
    total = d["MemTotal"]
    avail = d.get("MemAvailable", d["MemFree"])
    st = d.get("SwapTotal", 0)
    return {"total": total, "used": total - avail, "available": avail,
            "swap_total": st, "swap_used": st - d.get("SwapFree", 0)}


def disk():
    s = os.statvfs("/")
    total = s.f_blocks * s.f_frsize
    return {"total": total, "used": total - s.f_bavail * s.f_frsize}


def net():
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in list(f)[2:]:
            name, data = line.split(":", 1)
            if name.strip() == "lo":
                continue
            p = data.split()
            rx += int(p[0])
            tx += int(p[8])
    return {"rx": rx, "tx": tx}


def services():
    out = {}
    for s in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", s],
                               capture_output=True, text=True, timeout=3)
            out[s] = r.stdout.strip() or "unknown"
        except Exception:
            out[s] = "unknown"
    return out


CPU_NAME = cpu_name()


def payload():
    with open("/proc/uptime") as f:
        up = float(f.read().split()[0])
    return {
        "host": socket.gethostname(),
        "cpu_name": CPU_NAME,
        "cores_count": os.cpu_count(),
        "cpu_percent": STATE["cpu"],
        "cores": STATE["cores"],
        "load": [round(x, 2) for x in os.getloadavg()],
        "mem": meminfo(),
        "disk": disk(),
        "net": net(),
        "uptime": int(up),
        "services": services(),
        "time": int(time.time()),
    }


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        key = self.headers.get("X-Key") or parse_qs(u.query).get("key", [""])[0]
        if not KEY or not hmac.compare_digest(key.encode(), KEY.encode()):
            self.send_response(403)
            self.end_headers()
            return
        if u.path != "/stats":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=cpu_thread, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
PYEOF

cat > /etc/systemd/system/stats.service << 'EOF'
[Unit]
Description=Server stats API
After=network.target

[Service]
EnvironmentFile=/root/stats/.env
ExecStart=/usr/bin/python3 /root/stats/stats.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

echo "== 6/6 Автообновление ботов с GitHub =="
cat > /root/update-bot.sh << 'EOF'
#!/bin/bash
BASE="https://raw.githubusercontent.com/Oleg-Brysin2010/Under-Repository/main"
update() {
  TMP=$(mktemp)
  curl -fsS -m 30 -o "$TMP" "$BASE/$1?t=$(date +%s)" || { rm -f "$TMP"; return; }
  [ -s "$TMP" ] || { rm -f "$TMP"; return; }
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$TMP" || { rm -f "$TMP"; return; }
  if ! cmp -s "$TMP" "$2"; then
    [ -f "$2" ] && cp "$2" "$2.bak"
    cp "$TMP" "$2"
    systemctl restart "$3"
    logger -t update-bot "$1 обновлён"
  fi
  rm -f "$TMP"
}
update bot.py /root/bot/bot.py bot
update bot2.py /root/bot2/bot2.py bot2
EOF
chmod +x /root/update-bot.sh

cat > /etc/systemd/system/update-bot.service << 'EOF'
[Unit]
Description=Update bots from GitHub

[Service]
Type=oneshot
ExecStart=/root/update-bot.sh
EOF

cat > /etc/systemd/system/update-bot.timer << 'EOF'
[Unit]
Description=Check GitHub for bot updates

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now bot
if [ -n "$TOKEN2" ] && [ -f /root/bot2/bot2.py ]; then
  systemctl enable --now bot2
else
  systemctl enable bot2
fi
systemctl enable --now stats
systemctl enable --now update-bot.timer
sleep 5

IP=$(hostname -I | awk '{print $1}')
echo
echo "================ ГОТОВО ================"
echo "Статус служб:"
for s in bot bot2 stats update-bot.timer; do
  echo "  $s: $(systemctl is-active $s)"
done
echo
echo "IP сервера: $IP"
echo "Проверка мониторинга в браузере телефона:"
echo "  http://$IP:8080/stats?key=КЛЮЧ"
echo "Ключ показать (не отправляй его и не светись на скринах):"
echo "  cut -d= -f2 /root/stats/.env"
echo
echo "Теперь напиши боту /start в Telegram."
