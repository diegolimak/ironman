"""
garmin_auth.py — Autenticação Garmin Connect
Versão robusta com 3 métodos de auth para contornar rate limit (429).

OPÇÕES:
  1 - Email + senha (funciona quando não há rate limit)
  2 - Via cookies do Chrome (funciona sempre, você só precisa estar logado no browser)
  3 - Testar token existente

Uso:
    python garmin_auth.py
"""
import os, sys, json, time, datetime

TOKEN_DIR = os.path.join(os.path.dirname(__file__), ".garmin_tokens")

def log(msg):
    print(msg)

def check_deps():
    for pkg in ["garminconnect", "garth", "requests", "browser_cookie3"]:
        try:
            __import__(pkg.replace("-","_"))
        except ImportError:
            log(f"  Instalando {pkg}...")
            os.system(f"{sys.executable} -m pip install {pkg} --quiet")

# ─────────────────────────────────────────────
#  MÉTODO 1: Email + Senha
# ─────────────────────────────────────────────
def auth_password():
    import getpass
    from garminconnect import Garmin

    log("\n📧 MÉTODO 1: Email + Senha")
    log("─" * 40)
    email    = input("Email Garmin Connect: ").strip()
    password = getpass.getpass("Senha: ")

    for attempt in range(1, 4):
        log(f"\nTentativa {attempt}/3...")
        try:
            api = Garmin(email=email, password=password)
            api.login()
            os.makedirs(TOKEN_DIR, exist_ok=True)
            api.garth.dump(TOKEN_DIR)
            log(f"✅ Autenticado! Token salvo.")
            return True
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                wait = 45 * attempt
                log(f"⚠  Rate limit (429) — aguardando {wait}s...")
                time.sleep(wait)
            else:
                log(f"❌ Erro: {e}")
                return False

    log("\n❌ Rate limit persistente após 3 tentativas.")
    log("   → Tente o MÉTODO 2 (cookies do browser) ou aguarde 30-60 min.")
    return False

# ─────────────────────────────────────────────
#  MÉTODO 2: Cookies do Chrome (sem rate limit!)
# ─────────────────────────────────────────────
def auth_via_chrome():
    """
    Pega os cookies do Chrome onde você já está logado no Garmin Connect,
    e usa esses cookies para obter um token OAuth2 real.
    Sem nenhuma requisição de login → sem rate limit!
    """
    log("\n🌐 MÉTODO 2: Cookies do Chrome (sem rate limit)")
    log("─" * 40)
    log("\nPasso 1: Abra https://connect.garmin.com no Chrome e faça login")
    input("Pressione ENTER quando estiver logado no Garmin Connect no Chrome...")

    try:
        import requests
        cookies = None

        # ── Tentativa 1: arquivo manual exportado via Cookie-Editor ──
        manual_file = os.path.join(TOKEN_DIR, "chrome_cookies.json")
        if os.path.exists(manual_file):
            log(f"\nArquivo de cookies encontrado: {manual_file}")
            with open(manual_file) as f:
                raw = json.load(f)
            # Suporta formato Cookie-Editor (lista de dicts) ou dict simples
            if isinstance(raw, list):
                cookies = {c['name']: c['value'] for c in raw if c.get('value')}
            else:
                cookies = raw
            if cookies:
                log(f"  ✓ {len(cookies)} cookies carregados")

        # ── Tentativa 2: browser_cookie3 (funciona no Chrome < v127) ──
        if not cookies:
            try:
                import browser_cookie3
                log("\nLendo cookies do Chrome via browser_cookie3...")
                cj = browser_cookie3.chrome(domain_name='.garmin.com')
                cookies = {c.name: c.value for c in cj}
                if cookies:
                    log(f"  ✓ {len(cookies)} cookies encontrados")
            except Exception as e:
                log(f"  ⚠ browser_cookie3 não funcionou: {e}")

        # ── Sem cookies → instruções para exportar manualmente ──
        if not cookies:
            os.makedirs(TOKEN_DIR, exist_ok=True)
            log("\n" + "═"*55)
            log("  Chrome v127+ bloqueia acesso automático a cookies.")
            log("  Faça a exportação manual em 30 segundos:")
            log("═"*55)
            log("")
            log("  1. Abra o Chrome e acesse https://connect.garmin.com")
            log("     (certifique-se de estar LOGADO)")
            log("")
            log("  2. Instale a extensão gratuita 'Cookie-Editor':")
            log("     https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm")
            log("")
            log("  3. Na aba do Garmin Connect, clique no ícone da extensão")
            log("     → botão 'Export' → 'Export as JSON'")
            log("     (copia automaticamente para o clipboard)")
            log("")
            log(f"  4. Crie a pasta: {TOKEN_DIR}")
            log(f"     Abra o Notepad, cole (Ctrl+V) e salve como:")
            log(f"     chrome_cookies.json   (dentro da pasta acima)")
            log("")
            log("  5. Execute garmin_auth.py novamente — vai funcionar!")
            log("═"*55)
            return False

        log(f"  {len(cookies)} cookies encontrados: {list(cookies.keys())[:6]}...")

        # Verifica se temos a sessão SSO
        sso_keys = [k for k in cookies if 'JWT' in k or 'SSO' in k or 'TOKEN' in k or 'SESSION' in k]
        if not sso_keys:
            log("⚠  Cookies de sessão não encontrados. Certifique-se de estar logado.")

        # Usa os cookies para fazer uma requisição autenticada e obter o token OAuth2
        session = requests.Session()
        for name, value in cookies.items():
            session.cookies.set(name, value, domain='.garmin.com')

        # Requisição para obter o perfil (testa se os cookies são válidos)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "NK": "NT",
        }
        r = session.get(
            "https://connect.garmin.com/modern/proxy/userprofile-service/socialProfile",
            headers=headers
        )

        if r.status_code == 200:
            profile = r.json()
            display_name = profile.get("displayName", "Diego")
            log(f"✅ Sessão válida! Usuário: {display_name}")
        else:
            log(f"⚠  Status {r.status_code} — talvez seja necessário relogar no browser")
            if r.status_code == 401:
                log("   → Acesse https://connect.garmin.com e faça login, depois tente novamente.")
                return False

        # Obtém o OAuth2 token via endpoint de exchange
        r2 = session.post(
            "https://connect.garmin.com/services/auth/token/exchange",
            headers={**headers, "origin": "https://connect.garmin.com"},
        )

        if r2.status_code == 200:
            token_data = r2.json()
            os.makedirs(TOKEN_DIR, exist_ok=True)

            # Salva no formato que o garth/garminconnect espera
            oauth2 = {
                "scope":         token_data.get("scope", ""),
                "jti":           token_data.get("jti", ""),
                "token_type":    "Bearer",
                "access_token":  token_data.get("access_token", ""),
                "refresh_token": token_data.get("refresh_token", ""),
                "expires_in":    token_data.get("expires_in", 3600),
                "expires_at":    time.time() + token_data.get("expires_in", 3600),
            }

            with open(os.path.join(TOKEN_DIR, "oauth2_token.json"), "w") as f:
                json.dump(oauth2, f, indent=2)

            log(f"✅ Token OAuth2 salvo em {TOKEN_DIR}/oauth2_token.json")
        else:
            # Fallback: salva os cookies para uso direto
            os.makedirs(TOKEN_DIR, exist_ok=True)
            with open(os.path.join(TOKEN_DIR, "session_cookies.json"), "w") as f:
                json.dump(cookies, f, indent=2)
            log(f"✅ Cookies salvos (fallback). Sync pode funcionar com sessão de browser.")

        # Salva também os cookies raw para fallback
        with open(os.path.join(TOKEN_DIR, "chrome_cookies.json"), "w") as f:
            json.dump(cookies, f, indent=2)

        return True

    except ImportError as e:
        log(f"❌ Biblioteca ausente: {e}")
        log("   Execute: pip install browser-cookie3")
        return False
    except Exception as e:
        log(f"❌ Erro: {e}")
        import traceback; traceback.print_exc()
        return False

# ─────────────────────────────────────────────
#  TESTE
# ─────────────────────────────────────────────
def test_auth():
    try:
        from garminconnect import Garmin
        import garth

        api = Garmin()
        api.garth.load(TOKEN_DIR)

        today = datetime.date.today().isoformat()
        acts = api.get_activities_by_date(today, today)
        log(f"✅ Autenticação OK! Atividades hoje: {len(acts or [])}")

        # Testa sleep
        try:
            sleep = api.get_sleep_data(today)
            if sleep:
                dto = sleep.get("dailySleepDTO", sleep)
                hrs = (dto.get("sleepTimeSeconds") or dto.get("totalSleepSeconds") or 0) / 3600
                log(f"   Sono: {hrs:.1f}h")
        except Exception as e:
            log(f"   Sleep: {e}")

        return True
    except FileNotFoundError:
        log("❌ Token não encontrado. Execute garmin_auth.py para autenticar.")
        return False
    except Exception as e:
        log(f"❌ Token inválido: {e}")
        return False

# ─────────────────────────────────────────────
#  TEST via cookies (sync sem OAuth formal)
# ─────────────────────────────────────────────
def test_with_cookies():
    """Testa usando cookies do Chrome diretamente."""
    cookie_file = os.path.join(TOKEN_DIR, "chrome_cookies.json")
    if not os.path.exists(cookie_file):
        return False
    try:
        import requests
        with open(cookie_file) as f:
            cookies = json.load(f)
        session = requests.Session()
        for name, value in cookies.items():
            session.cookies.set(name, value, domain='.garmin.com')
        r = session.get(
            "https://connect.garmin.com/modern/proxy/userprofile-service/socialProfile",
            headers={"NK":"NT","User-Agent":"Mozilla/5.0"},
        )
        if r.status_code == 200:
            name = r.json().get("displayName","?")
            log(f"✅ Cookies válidos! Usuário: {name}")
            return True
    except Exception as e:
        log(f"⚠ Teste de cookie: {e}")
    return False

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    check_deps()
    print()

    # Verifica token existente
    if os.path.isdir(TOKEN_DIR):
        files = os.listdir(TOKEN_DIR)
        if "oauth2_token.json" in files or "oauth1_token.json" in files:
            log("Token encontrado. Testando...")
            if test_auth():
                log("\n✅ Autenticação já configurada! Pronto para usar.")
                sys.exit(0)

        if "chrome_cookies.json" in files:
            log("Cookies encontrados. Testando...")
            if test_with_cookies():
                log("\n✅ Sessão Chrome válida!")
                sys.exit(0)

    print("╔════════════════════════════════════════════╗")
    print("║   Garmin Connect — Configuração inicial    ║")
    print("╠════════════════════════════════════════════╣")
    print("║  1 — Email + senha                         ║")
    print("║  2 — Via Chrome (sem rate limit!) ✅ REC   ║")
    print("║  3 — Testar token existente                ║")
    print("╚════════════════════════════════════════════╝")

    choice = input("\nOpção [1/2/3, padrão=2]: ").strip() or "2"

    if choice == "1":
        ok = auth_password()
        if not ok:
            log("\nFalhando para o método browser...")
            auth_via_chrome()
    elif choice == "3":
        test_auth() or test_with_cookies()
    else:
        auth_via_chrome()
