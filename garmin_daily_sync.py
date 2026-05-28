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
    if not os.path.isdir(TOKEN_DIR):
        log("❌ Token não encontrado. Execute garmin_get_token.py primeiro.")
        sys.exit(1)

    # ── Prioridade 1: Bearer token (garmin_get_token.py) ──
    oauth2_file = os.path.join(TOKEN_DIR, "oauth2_token.json")
    if os.path.exists(oauth2_file):
        try:
            with open(oauth2_file) as f:
                tok = json.load(f)
            access_token = tok.get("access_token", "")
            if access_token:
                client = BearerGarminClient(access_token)
                test = client._get("/userprofile-service/userprofile")
                log(f"✓ Conectado via Bearer token")
                return client
        except Exception as e:
            log(f"⚠ Bearer token falhou ({e}), tentando garth...")

    # ── Fallback: garth OAuth2 ──
    try:
        from garminconnect import Garmin
        api = Garmin()
        api.garth.load(TOKEN_DIR)
        log(f"✓ Conectado via garth OAuth2")
        return api
    except Exception as e:
        log(f"⚠ garth falhou: {e}")

    log("❌ Nenhum auth disponível. Execute:  python garmin_get_token.py")
    sys.exit(1)


class BearerGarminClient:
    """Cliente REST usando Bearer JWT token do Garmin Connect."""
    BASE = "https://connect.garmin.com/gc-api"

    def __init__(self, access_token):
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "NK": "NT",
            "Accept": "application/json",
            "Origin": "https://connect.garmin.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def _get(self, path, **params):
        import requests
        r = self.session.get(self.BASE + path, params=params)
        r.raise_for_status()
        return r.json() if r.content else {}

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
