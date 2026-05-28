"""
garmin_daily_sync.py — Sincronização diária de dados Garmin Connect
Agendado para rodar todo dia às 9h via Task Scheduler do Windows.
Atualiza garmin_data.json que o dashboard lê automaticamente.

Dados coletados: sono, stress, HRV, body battery, passos, FC repouso,
                 treinos do dia, recovery advisor, intensidade.

Uso:
    python garmin_daily_sync.py
"""
import os, sys, json, datetime, traceback

TOKEN_DIR  = os.path.join(os.path.dirname(__file__), ".garmin_tokens")
OUTPUT     = os.path.join(os.path.dirname(__file__), "garmin_data.json")
LOG_FILE   = os.path.join(os.path.dirname(__file__), "garmin_sync.log")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_garmin():
    try:
        from garminconnect import Garmin
    except ImportError:
        log("Instalando garminconnect...")
        os.system(f"{sys.executable} -m pip install garminconnect garth")
        from garminconnect import Garmin

    if not os.path.isdir(TOKEN_DIR):
        log("❌ Token não encontrado. Execute garmin_auth.py primeiro.")
        sys.exit(1)

    # Tenta OAuth token primeiro
    oauth2_file = os.path.join(TOKEN_DIR, "oauth2_token.json")
    if os.path.exists(oauth2_file):
        try:
            client = Garmin()
            client.garth.load(TOKEN_DIR)
            log(f"✓ Conectado via OAuth2")
            return client
        except Exception as e:
            log(f"⚠ OAuth falhou ({e}), tentando cookies...")

    # Fallback: usa cookies do Chrome para sync via requests direto
    cookie_file = os.path.join(TOKEN_DIR, "chrome_cookies.json")
    if os.path.exists(cookie_file):
        log("✓ Usando sessão Chrome (cookie-based)")
        return load_garmin_via_cookies(cookie_file)

    log("❌ Nenhum método de auth disponível. Execute garmin_auth.py primeiro.")
    sys.exit(1)

def load_garmin_via_cookies(cookie_file):
    """Garmin client que usa cookies do browser diretamente."""
    import requests, json

    with open(cookie_file) as f:
        cookies = json.load(f)

    class GarminCookieClient:
        BASE = "https://connect.garmin.com/modern/proxy"
        def __init__(self):
            self.session = requests.Session()
            for name, value in cookies.items():
                self.session.cookies.set(name, value, domain='.garmin.com')
            self.session.headers.update({
                "NK": "NT",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            self.display_name = "Diego"

        def _get(self, url, **params):
            r = self.session.get(self.BASE + url, params=params)
            r.raise_for_status()
            return r.json()

        def get_sleep_data(self, date):
            return self._get(f"/wellness-service/wellness/dailySleepData/{date}")

        def get_stress_data(self, date):
            return self._get(f"/wellness-service/wellness/dailyStress/{date}")

        def get_hrv_data(self, date):
            return self._get(f"/hrv-service/hrv/{date}")

        def get_body_battery(self, start, end):
            return self._get(f"/wellness-service/wellness/bodyBattery/range/{start}/{end}")

        def get_rhr_day(self, date):
            return self._get(f"/wellness-service/wellness/dailyHeartRate/{date}")

        def get_steps_data(self, date):
            return self._get(f"/wellness-service/wellness/dailySummaryChart/{date}")

        def get_activities_by_date(self, start, end):
            return self._get("/activitylist-service/activities/search/activities",
                           startDate=start, endDate=end)

        def get_activities(self, start, limit):
            return self._get("/activitylist-service/activities/search/activities",
                           start=start, limit=limit)

        def get_training_status(self, date):
            return self._get(f"/metrics-service/metrics/trainingStatus/{date}")

        def get_max_metrics(self, date):
            return self._get(f"/metrics-service/metrics/maxMetrics/{date}")

        def get_full_name(self):
            try:
                p = self._get("/userprofile-service/socialProfile")
                return p.get("displayName", "Diego")
            except:
                return "Diego"

    client = GarminCookieClient()
    try:
        client.display_name = client.get_full_name()
        log(f"✓ Conectado via Chrome cookies: {client.display_name}")
    except Exception as e:
        log(f"⚠ Cookies podem estar expirados: {e}")
        log("  → Execute garmin_auth.py (opção 2) para renovar")
    return client

def safe_get(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as e:
        log(f"  ⚠ {fn.__name__ if hasattr(fn,'__name__') else 'call'}: {e}")
        return default

def run():
    log("═══ Garmin Daily Sync START ═══")
    client = load_garmin()

    today     = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    data = {
        "last_sync":  datetime.datetime.now().isoformat(),
        "date":       today,
        "athlete":    "Diego",
    }

    # ── Sono ──
    log("Pulling sleep...")
    sleep = safe_get(client.get_sleep_data, today)
    if sleep is None:
        sleep = safe_get(client.get_sleep_data, yesterday)
    data["sleep"] = sleep
    if sleep:
        dto = sleep.get("dailySleepDTO", {}) or sleep
        hrs = (dto.get("sleepTimeSeconds") or dto.get("totalSleepSeconds") or 0) / 3600
        log(f"  Sono: {hrs:.1f}h")

    # ── Stress ──
    log("Pulling stress...")
    stress = safe_get(client.get_stress_data, today)
    data["stress"] = stress
    if stress:
        avg = stress.get("overallStressLevel") or stress.get("averageStressLevel")
        log(f"  Stress médio: {avg}")
        data["overallStressLevel"] = avg

    # ── HRV ──
    log("Pulling HRV...")
    hrv = safe_get(client.get_hrv_data, today)
    data["hrv"] = hrv
    if hrv:
        summary = hrv.get("hrvSummary", {}) or hrv
        val = summary.get("lastNight5MinHighHrv") or summary.get("weeklyAvg")
        log(f"  HRV: {val}ms")
        if val:
            data["lastNight5MinHighHrv"] = val

    # ── Body Battery ──
    log("Pulling Body Battery...")
    bb_list = safe_get(client.get_body_battery, today, today)
    data["bodyBattery"] = bb_list
    if bb_list and isinstance(bb_list, list) and len(bb_list) > 0:
        last_bb = bb_list[-1]
        val = last_bb.get("charged") or last_bb.get("value") or last_bb.get("bodyBatteryMostRecentValue")
        log(f"  Body Battery: {val}%")
        if val:
            data["bodyBatteryMostRecentValue"] = val

    # ── FC Repouso ──
    log("Pulling RHR...")
    rhr = safe_get(client.get_rhr_day, today)
    data["rhr"] = rhr
    if rhr:
        val = rhr.get("value") or rhr.get("restingHeartRate")
        log(f"  FC Repouso: {val} bpm")
        data["restingHeartRate"] = val

    # ── Passos ──
    log("Pulling steps...")
    steps = safe_get(client.get_steps_data, today)
    data["steps"] = steps

    # ── Treinos do dia ──
    log("Pulling today activities...")
    acts = safe_get(client.get_activities_by_date, today, today)
    data["activities_today"] = acts
    if acts:
        log(f"  {len(acts)} atividade(s) hoje")

    # ── Últimas 7 atividades ──
    log("Pulling last 7 activities...")
    recent = safe_get(client.get_activities, 0, 7)
    data["recent_activities"] = recent
    if recent:
        for a in recent[:3]:
            nm = a.get("activityName","")
            dt = a.get("startTimeLocal","")[:10]
            log(f"  [{dt}] {nm}")

    # ── Training Status ──
    log("Pulling training status...")
    ts = safe_get(client.get_training_status, today)
    data["training_status"] = ts

    # ── Fitness Age / VO2max ──
    log("Pulling fitness data...")
    fitness = safe_get(client.get_max_metrics, today)
    data["fitness"] = fitness
    if fitness:
        vo2 = fitness.get("vo2MaxPreciseValue") or fitness.get("vo2MaxValue")
        log(f"  VO2máx: {vo2}")
        if vo2:
            data["vo2max"] = vo2

    # ── Gravar JSON ──
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
    log(f"✅ garmin_data.json atualizado ({OUTPUT})")

    # ── Atualizar index.html (force refresh cache) ──
    log("═══ Garmin Daily Sync DONE ═══\n")

if __name__ == "__main__":
    try:
        run()
    except Exception:
        log("❌ ERRO INESPERADO:")
        log(traceback.format_exc())
        sys.exit(1)
