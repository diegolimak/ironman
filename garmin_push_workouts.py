"""
garmin_push_workouts.py — Envia os treinos da semana para o Garmin Connect
Agendado para rodar toda segunda-feira via Task Scheduler.
Os treinos aparecem no relógio após a sincronização automática do Garmin.

Uso:
    python garmin_push_workouts.py [semana_num]   # semana_num: 1-11 (padrão: detecta pelo dia)
"""
import os, sys, json, datetime, time

TOKEN_DIR = os.path.join(os.path.dirname(__file__), ".garmin_tokens")
LOG_FILE  = os.path.join(os.path.dirname(__file__), "garmin_sync.log")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ── Traduz os dias do plano para datas reais ──
WEEK_STARTS = [
    datetime.date(2026, 5, 25),  # S1
    datetime.date(2026, 6,  1),  # S2
    datetime.date(2026, 6,  8),  # S3
    datetime.date(2026, 6, 15),  # S4
    datetime.date(2026, 6, 22),  # S5
    datetime.date(2026, 6, 29),  # S6
    datetime.date(2026, 7,  6),  # S7
    datetime.date(2026, 7, 13),  # S8
    datetime.date(2026, 7, 20),  # S9
    datetime.date(2026, 7, 27),  # S10
    datetime.date(2026, 8,  3),  # S11
]

DOW_PT = { "Seg":0, "Ter":1, "Qua":2, "Qui":3, "Sex":4, "Sáb":5, "Dom":6 }

SPORT_MAP = {
    "swim":     ("swimming",          "lap_swimming",        4),   # 4 = swimming
    "bike":     ("cycling",           "cycling",             2),   # 2 = cycling
    "run":      ("running",           "running",             1),   # 1 = running
    "brick":    ("multi_sport",       "other",               15),  # 15 = multi_sport
    "strength": ("strength_training", "strength_training",   5),   # 5 = strength
    "rest":     None,
    "race":     ("multi_sport",       "other",               15),
}

SPORT_TYPE_ID = {k: v[2] for k, v in SPORT_MAP.items() if v}

ZONE_TARGETS = {
    "Z1":  {"targetHrZone": 1},
    "Z2":  {"targetHrZone": 2},
    "Z3":  {"targetHrZone": 3},
    "Z4":  {"targetHrZone": 4},
    "Z1/Z2": {"targetHrZone": 2},
    "Z2/Z3": {"targetHrZone": 3},
    "Z3/Z4": {"targetHrZone": 4},
    "REST": {},
}

def detect_week_idx():
    today = datetime.date.today()
    idx = 0
    for i in range(len(WEEK_STARTS) - 1, -1, -1):
        if today >= WEEK_STARTS[i]:
            idx = i
            break
    return idx

def day_str_to_date(week_idx: int, dow_str: str) -> datetime.date:
    """Converts 'Ter 26/05' → actual date based on week start."""
    dow_prefix = dow_str.split(" ")[0]  # e.g. "Ter"
    offset = DOW_PT.get(dow_prefix, 0)
    week_start = WEEK_STARTS[week_idx]
    return week_start + datetime.timedelta(days=offset)

def build_workout_payload(day: dict, scheduled_date: datetime.date) -> dict:
    """Build Garmin workout JSON for a single training day."""
    sport_info = SPORT_MAP.get(day["disc"])
    if not sport_info:
        return None

    sport_type, sub_sport, sport_type_id = sport_info
    steps_raw = [s.strip() for s in day["detail"].split("|") if s.strip()]

    # Build workout steps
    workout_steps = []
    step_order = 1

    # Warm-up step (if "aquecimento" in steps)
    warmup = next((s for s in steps_raw if "aquecimento" in s.lower() or "aq " in s.lower()), None)
    if warmup:
        workout_steps.append({
            "type": "ExecutableStepDTO",
            "stepOrder": step_order,
            "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
            "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "time"},
            "endConditionValue": 600,  # 10min default
            "description": warmup[:100],
        })
        step_order += 1

    # Main body step
    zone = day.get("zone", "Z2")
    hr_zone = ZONE_TARGETS.get(zone, {}).get("targetHrZone", 2)

    workout_steps.append({
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
        "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "time"},
        "endConditionValue": parse_duration_seconds(day.get("dur", "60min")),
        "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
        "targetValueOne": hr_zone,
        "targetValueTwo": hr_zone,
        "description": day["name"][:100],
    })
    step_order += 1

    # Cool-down
    workout_steps.append({
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
        "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "time"},
        "endConditionValue": 300,  # 5min
        "description": "Resfriamento",
    })

    return {
        "workoutName": f"[IRONMAN] {day['name'][:50]}",
        "description": f"Semana do plano IRONMAN 70.3 Rio | {day['zone']} | {day['dur']}",
        "sportType": {"sportTypeId": sport_type_id, "sportTypeKey": sport_type},
        "subSportType": sub_sport,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": sport_type_id, "sportTypeKey": sport_type},
            "workoutSteps": workout_steps,
        }],
        "estimatedDurationInSecs": parse_duration_seconds(day.get("dur", "60min")),
        "scheduledDate": scheduled_date.isoformat(),
    }

def parse_duration_seconds(dur_str: str) -> int:
    """Parse '1h30' or '90min' or '2h45' into seconds."""
    dur_str = dur_str.lower().replace(" ", "").replace("—","0min")
    total = 0
    if "h" in dur_str:
        parts = dur_str.split("h")
        try: total += int(parts[0]) * 3600
        except: pass
        if len(parts) > 1:
            mn = parts[1].replace("min","").replace("m","")
            try: total += int(mn) * 60
            except: pass
    elif "min" in dur_str or "m" in dur_str:
        mn = dur_str.replace("min","").replace("m","")
        try: total = int(mn) * 60
        except: pass
    return max(total, 1800)  # minimum 30min

def load_garmin_client():
    """Load Garmin client — tries Bearer token, then OAuth2 via garth."""
    import requests as _req

    if not os.path.isdir(TOKEN_DIR):
        log("❌ Token não encontrado. Execute garmin_get_token.py primeiro.")
        sys.exit(1)

    # ── Prioridade 1: Bearer token manual (garmin_get_token.py) ──
    oauth2_file = os.path.join(TOKEN_DIR, "oauth2_token.json")
    if os.path.exists(oauth2_file):
        with open(oauth2_file) as f:
            tok = json.load(f)
        access_token = tok.get("access_token", "")
        if access_token:
            client = BearerGarminClient(access_token)
            # Testa se funciona
            try:
                r = client._get("/userprofile-service/userprofile")
                if r is not None:
                    log("✓ Conectado via Bearer token")
                    return client
            except Exception as e:
                log(f"⚠ Bearer token inválido ({e})")

    # ── Fallback: garminconnect OAuth2 via garth ──
    try:
        from garminconnect import Garmin
        api = Garmin()
        api.garth.load(TOKEN_DIR)
        log("✓ Conectado via garth OAuth2")
        return api
    except Exception as e:
        log(f"⚠ garth falhou: {e}")

    log("❌ Nenhum método de auth disponível.")
    log("   Execute:  python garmin_get_token.py")
    sys.exit(1)

class BearerGarminClient:
    """Cliente REST usando Bearer token JWT do Garmin Connect."""
    BASE = "https://connect.garmin.com/gc-api"

    def __init__(self, access_token):
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "NK": "NT",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://connect.garmin.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def _get(self, path, **params):
        r = self.session.get(self.BASE + path, params=params)
        r.raise_for_status()
        return r.json() if r.content else {}

    def _post(self, path, body=None, **kwargs):
        r = self.session.post(self.BASE + path, json=body, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else {}

    def add_workout(self, payload):
        return self._post("/workout-service/workout", payload)

    def schedule_workout(self, workout_id, date_str):
        return self._post(f"/workout-service/schedule/{workout_id}", body={"date": date_str})

def push_workouts(week_idx: int):
    client = load_garmin_client()
    log(f"✓ Conectado ao Garmin Connect")

    # Load week data from JS file (parse it)
    dashboard = os.path.join(os.path.dirname(__file__), "ironman_dashboard.html")
    if not os.path.exists(dashboard):
        log("❌ Dashboard não encontrado.")
        sys.exit(1)

    # Read the WEEKS array from garmin_data.json if it exists, else parse from HTML
    # For simplicity, we'll use the hardcoded week index
    week_start = WEEK_STARTS[week_idx]
    log(f"Semana {week_idx+1}/11 · início: {week_start}")

    # Import week data
    sys.path.insert(0, os.path.dirname(__file__))
    week_data = get_week_days(week_idx)

    pushed = 0
    skipped = 0

    for day in week_data:
        if day["disc"] == "rest":
            skipped += 1
            continue

        sched_date = day_str_to_date(week_idx, day["dow"])
        payload = build_workout_payload(day, sched_date)
        if not payload:
            skipped += 1
            continue

        try:
            result = client.add_workout(payload)
            wo_id = result.get("workoutId") if isinstance(result, dict) else None

            # Schedule it on the calendar
            if wo_id:
                client.schedule_workout(wo_id, sched_date.isoformat())
                log(f"  ✅ [{sched_date}] {day['name'][:45]}")
            else:
                log(f"  ⚠ Sem workoutId para {day['name'][:45]}")

            pushed += 1
            time.sleep(0.8)  # rate limiting

        except Exception as e:
            log(f"  ❌ Erro ao enviar {day['name'][:40]}: {e}")

    log(f"\n📱 {pushed} treinos enviados para o Garmin Connect")
    log("   Sincronize o relógio para ver os treinos agendados.")
    if skipped:
        log(f"   ({skipped} ignorados: descanso/sem sport)")

def get_week_days(week_idx: int) -> list:
    """
    Returns hardcoded days list for given week index.
    In production, this reads from the HTML/JS WEEKS array.
    We re-define a minimal version here for the script.
    """
    # For now, read from garmin_data.json if we stored them there,
    # or fall back to reading the HTML
    # Quick approach: re-parse the HTML file
    import re
    html_path = os.path.join(os.path.dirname(__file__), "ironman_dashboard.html")
    with open(html_path, encoding="utf-8") as f:
        content = f.read()

    # Find the specific week by id
    week_id = f"w{week_idx+1:02d}"
    # Extract the days array for this week
    pattern = rf"id:'{week_id}'.*?days:\[(.*?)\]\s*\}}"
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        log(f"⚠ Semana {week_id} não encontrada no HTML")
        return []

    days_raw = m.group(1)
    # Parse each day object
    days = []
    day_pat = re.compile(
        r"\{id:'(?P<id>[^']+)',dow:'(?P<dow>[^']+)',disc:'(?P<disc>[^']+)',"
        r"icon:'[^']*',name:\"(?P<name>[^\"]+)\",detail:\"(?P<detail>[^\"]+)\","
        r"dur:'(?P<dur>[^']+)',zone:'(?P<zone>[^']+)'",
        re.DOTALL
    )
    for dm in day_pat.finditer(days_raw):
        days.append(dm.groupdict())
    log(f"  Encontrados {len(days)} dias para {week_id}")
    return days

if __name__ == "__main__":
    log("═══ Garmin Push Workouts START ═══")

    if len(sys.argv) > 1:
        try:
            week_num = int(sys.argv[1])
            week_idx = max(0, min(10, week_num - 1))
        except:
            week_idx = detect_week_idx()
    else:
        week_idx = detect_week_idx()

    log(f"Semana selecionada: {week_idx + 1}/11")
    push_workouts(week_idx)
    log("═══ Garmin Push Workouts DONE ═══\n")
