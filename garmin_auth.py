"""
garmin_auth.py — Configuração inicial da autenticação Garmin Connect
Execute UMA VEZ para salvar o token OAuth localmente.
Após isso, garmin_daily_sync.py e garmin_push_workouts.py funcionam sem senha.

Uso:
    python garmin_auth.py
"""
import os, sys, getpass

try:
    import garth
    from garminconnect import Garmin
except ImportError:
    print("Instalando dependências Garmin...")
    os.system(f"{sys.executable} -m pip install garminconnect garth")
    import garth
    from garminconnect import Garmin

TOKEN_DIR = os.path.join(os.path.dirname(__file__), ".garmin_tokens")

def do_auth():
    print("\n╔══════════════════════════════════════╗")
    print("║  Autenticação Garmin Connect          ║")
    print("╚══════════════════════════════════════╝\n")

    email    = input("Email Garmin Connect: ").strip()
    password = getpass.getpass("Senha Garmin Connect: ")

    print("\nConectando...")
    try:
        client = Garmin(email, password)
        client.login()
        client.garth.dump(TOKEN_DIR)
        print(f"\n✅ Autenticado com sucesso! Token salvo em: {TOKEN_DIR}")
        print(f"   Usuário: {client.get_full_name()}")
        print("\nAgora execute garmin_daily_sync.py para puxar dados.\n")
    except Exception as e:
        print(f"\n❌ Erro de autenticação: {e}")
        print("Verifique email/senha e tente novamente.")
        sys.exit(1)

if __name__ == "__main__":
    do_auth()
