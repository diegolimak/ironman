"""
garmin_get_token.py — Extrai o Bearer token do Garmin Connect via Chrome DevTools
Só precisa rodar UMA vez (ou quando o token expirar, ~1 ano com refresh_token).

Como usar:
  1. Abra Chrome em https://connect.garmin.com (logado)
  2. Aperte F12 -> aba "Network" -> marque "Preserve log"
  3. Clique em qualquer coisa no Garmin Connect (ex: Activities, Dashboard)
  4. No campo Filter (Network tab), digite: /proxy/
  5. Clique em qualquer requisição listada
  6. Em "Request Headers", encontre:  Authorization: Bearer eyJ...
  7. Copie TUDO depois de "Bearer " (o token começa com eyJ)
  8. Execute este script e cole o token quando pedido
"""
import os, sys, json, time, datetime

TOKEN_DIR = os.path.join(os.path.dirname(__file__), ".garmin_tokens")

def log(msg): print(msg)

def save_token_manual():
    log("\n" + "="*60)
    log("  EXTRAÇÃO DO TOKEN VIA CHROME DEVTOOLS")
    log("="*60)
    log("""
Passo a passo (2 minutos):

1. Chrome -> https://connect.garmin.com  (logado)
2. Pressione F12 -> clique na aba "Network"
3. Marque a caixa "Preserve log" (topo do painel)
4. Na barra de filtros, digite:  proxy
5. Clique em "Dashboard" ou "Activities" no menu do Garmin
6. Nas requisicoes listadas, clique em qualquer uma que tenha
   "proxy" na URL (ex: /proxy/userprofile-service/...)
7. Clique em "Request Headers" (lado direito)
8. Localize a linha:  Authorization: Bearer eyJh...
9. Selecione e copie TUDO o que vem APOS "Bearer "
   (o token tem ~1000 caracteres, comeca com eyJh)
""")
    log("-"*60)
    token = input("Cole o access_token aqui (Bearer eyJh...): ").strip()
    token = token.replace("Bearer ", "").strip()

    if not token.startswith("eyJ"):
        log("AVISO: token nao parece um JWT (deveria comecar com eyJh)")
        log(f"  Recebido: {token[:30]}...")

    # Tenta extrair expires_in do JWT (sem verificar assinatura)
    expires_in = 3600  # default 1h
    try:
        import base64
        parts = token.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=="
            payload = json.loads(base64.b64decode(padded).decode())
            exp = payload.get("exp", 0)
            iat = payload.get("iat", time.time())
            expires_in = max(int(exp - time.time()), 60) if exp else 3600
            log(f"Token JWT: sub={payload.get('sub','?')}, exp em {expires_in//3600}h {(expires_in%3600)//60}min")
    except Exception as e:
        log(f"  (nao consegui ler payload JWT: {e})")

    # Pede refresh token
    log("\nAgora o refresh_token (opcional mas recomendado para renovacao):")
    log("  No Chrome DevTools, procure uma requisicao POST para 'token' ou")
    log("  'refresh' no historico. Ou pode deixar em branco por enquanto.")
    refresh = input("Cole o refresh_token (ou pressione ENTER para pular): ").strip()

    os.makedirs(TOKEN_DIR, exist_ok=True)
    oauth2 = {
        "scope": "CONNECT_READ CONNECT_WRITE",
        "jti":   "manual_token",
        "token_type":    "Bearer",
        "access_token":  token,
        "refresh_token": refresh or token,
        "expires_in":    expires_in,
        "expires_at":    time.time() + expires_in,
    }
    out = os.path.join(TOKEN_DIR, "oauth2_token.json")
    with open(out, "w") as f:
        json.dump(oauth2, f, indent=2)
    log(f"\nSalvo em: {out}")

    # Testa imediatamente
    log("\nTestando token...")
    test_token(token)

def test_token(access_token=None):
    import requests
    if access_token is None:
        tf = os.path.join(TOKEN_DIR, "oauth2_token.json")
        with open(tf) as f:
            access_token = json.load(f)["access_token"]

    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "NK": "NT",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    tests = [
        ("Perfil",        "https://connect.garmin.com/gc-api/userprofile-service/userprofile"),
        ("Ultimas ativs", "https://connect.garmin.com/gc-api/activitylist-service/activities/search/activities?start=0&limit=3"),
    ]
    ok = 0
    for name, url in tests:
        r = requests.get(url, headers=hdrs)
        try:
            d = r.json()
            if d and d != {} and d != []:
                print(f"  OK {name}: {str(d)[:80]}")
                ok += 1
            else:
                print(f"  ?? {name}: resposta vazia (status {r.status_code})")
        except:
            print(f"  FAIL {name}: {r.status_code} {r.text[:60]}")

    if ok > 0:
        print(f"\nTOKEN VALIDO! {ok}/2 endpoints responderam.")
        print("Pode agora rodar:  python garmin_push_workouts.py")
    else:
        print("\nToken pode estar invalido ou expirado.")
        print("Tente novamente pegando um token mais recente do DevTools.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_token()
    else:
        save_token_manual()
