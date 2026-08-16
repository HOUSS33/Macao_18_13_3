"""
========================================================================================
BOT DE SIGNAL LIVE : WEBSOCKET -> MACHINE A ETATS -> TELEGRAM
========================================================================================
Version corrigée : c'est le CLIENT qui envoie le message "available" en premier
(pas le serveur), suivi du "subscribe" après un court délai. C'est cette
séquence exacte, confirmée fonctionnelle, qui débloque le flux de données.
========================================================================================
"""

import json
import time
import csv
import os
import requests
import websocket  # pip install websocket-client
from datetime import datetime

# ==========================================================================
# 0. ENREGISTREMENT CSV
# ==========================================================================
# 🔧 Si un Volume Railway est attaché, RAILWAY_VOLUME_MOUNT_PATH est injecté
# automatiquement (ex: "/data") et les données survivent aux redéploiements.
# En local (pas de volume), ça retombe simplement sur le dossier courant.
VOLUME_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")
CSV_FILE = os.path.join(VOLUME_PATH, "roulette_data.csv")
CSV_HEADERS = ["Timestamp", "GameID", "Result", "Color"]


def load_last_game_id_from_csv():
    """Lit le dernier gameId déjà enregistré, pour rattraper les spins
    manqués pendant une coupure/redémarrage (AVANT de recréer le header)."""
    if not os.path.exists(CSV_FILE):
        return None
    try:
        with open(CSV_FILE, newline='') as f:
            rows = list(csv.reader(f))
        if len(rows) <= 1:  # juste le header, ou fichier vide
            return None
        return rows[-1][1]  # colonne GameID de la dernière ligne
    except Exception as e:
        print(f"[CSV] Impossible de lire le dernier gameId : {e}")
        return None


if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)


def log_spin_to_csv(game_id, result, color, spin_time=None):
    # 🔧 Utilise l'heure RÉELLE du spin fournie par le serveur (entry["time"])
    # plutôt que l'heure de traitement — important pour les rattrapages, où
    # plusieurs spins sont traités d'un coup (donc datetime.now() serait
    # identique pour tous, ce qui fausserait les horodatages du CSV).
    timestamp = spin_time if spin_time else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [timestamp, game_id, result, color]
    with open(CSV_FILE, mode='a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)

# ==========================================================================
# 1. CONFIGURATION TELEGRAM
# ==========================================================================
TELEGRAM_BOT_TOKEN = "8963355040:AAF17IBO6-xxlXEJXtgQt2IwfJqCFO8B6j4"
TELEGRAM_CHAT_ID = "6098394153"

def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Telegram] Erreur d'envoi : {e}")


# ==========================================================================
# 2. CONFIGURATION WEBSOCKET + PROXY RÉSIDENTIEL
# ==========================================================================
WS_URL = "wss://dga.pragmaticplaylive.net/ws"

TABLE_KEY = "206"          # "Gates Of Olympus Roulette"
CURRENCY = "EUR"
CASINO_ID = "il9srgw4dna22222"  # valeur fixe observée dans la séquence fonctionnelle

WS_HEADERS = [
    "Accept-Encoding: gzip, deflate, br, zstd",
    "Accept-Language: en-US,en;q=0.9,fr-MA;q=0.8,fr;q=0.7",
    "Cache-Control: no-cache",
    "Pragma: no-cache",
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
]
WS_ORIGIN = "https://www.bigwinboard.com"

# 🔧 Identifiants DataImpulse — SOCKS5
PROXY_HOST = "gw.dataimpulse.com"
PROXY_PORT = 824
PROXY_TYPE = "socks5"
PROXY_LOGIN = "c28464d2322ae2cb5a09"
PROXY_PASSWORD = "afd6703a49960bd1"
USE_PROXY = True

DEBUG_TRACE = False  # remets à True si besoin de rediagnostiquer


# ==========================================================================
# 3. LA MACHINE A ETATS "EN LIGNE" (identique à roulette_strategy.py)
# ==========================================================================
def get_dozen(n):
    if 1 <= n <= 12: return 1
    if 13 <= n <= 24: return 2
    if 25 <= n <= 36: return 3
    return 0

def get_column(n):
    if n == 0: return 0
    if n % 3 == 1: return 1
    if n % 3 == 2: return 2
    if n % 3 == 0: return 3
    return 0


class LiveSignalEngine:
    def __init__(self, initial_capital, chaos_threshold=18, target_wins=1, base_unit=None):
        standard_fib = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233]
        requis_a_base_1 = sum(standard_fib)  # 609

        if base_unit is None:
            base_unit = max(1, initial_capital // requis_a_base_1)

        self.fib = [x * base_unit for x in standard_fib]
        self.actual_required_capital = sum(self.fib)
        self.chaos_threshold = chaos_threshold
        self.target_wins = target_wins

        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.total_real_deposits = initial_capital

        self.is_betting = False
        self.fib_index = 0
        self.current_mode = None
        self.target_value = None
        self.wins_in_current_signal = 0
        self.current_sequence_loss = 0

        self.streak_dozen = 0
        self.streak_column = 0

        self.p_doz = None
        self.p_col = None

        self.signal_counter = 0

    def process_spin(self, number):
        events = []
        c_doz = get_dozen(number)
        c_col = get_column(number)

        if self.p_doz is None:
            self.p_doz, self.p_col = c_doz, c_col
            return events

        if not self.is_betting:
            if c_doz != self.p_doz or c_doz == 0: self.streak_dozen += 1
            else: self.streak_dozen = 0

            if c_col != self.p_col or c_col == 0: self.streak_column += 1
            else: self.streak_column = 0

            if self.streak_dozen >= self.chaos_threshold and c_doz == 0:
                self.streak_dozen = 0
            if self.streak_column >= self.chaos_threshold and c_col == 0:
                self.streak_column = 0

            if self.streak_dozen >= self.chaos_threshold and c_doz != 0:
                self.is_betting, self.current_mode, self.target_value = True, 'dozen', c_doz
                self.fib_index, self.wins_in_current_signal = 0, 0
                self.streak_dozen, self.streak_column = 0, 0
                self.current_sequence_loss = 0
                self.signal_counter += 1
                events.append(
                    f"⚡ <b>SIGNAL #{self.signal_counter}</b> — DOZEN {c_doz}\n"
                    f"Mise à placer : <b>{self.fib[0]} DHS</b> sur la dizaine {c_doz}"
                )

            elif self.streak_column >= self.chaos_threshold and c_col != 0:
                self.is_betting, self.current_mode, self.target_value = True, 'column', c_col
                self.fib_index, self.wins_in_current_signal = 0, 0
                self.streak_dozen, self.streak_column = 0, 0
                self.current_sequence_loss = 0
                self.signal_counter += 1
                events.append(
                    f"⚡ <b>SIGNAL #{self.signal_counter}</b> — COLUMN {c_col}\n"
                    f"Mise à placer : <b>{self.fib[0]} DHS</b> sur la colonne {c_col}"
                )

            self.p_doz, self.p_col = c_doz, c_col
            return events

        bet_amount = self.fib[self.fib_index]
        actual_val = c_doz if self.current_mode == 'dozen' else c_col

        if actual_val == self.target_value and actual_val != 0:
            net_gain = bet_amount * 2
            self.capital += net_gain
            profit = net_gain - self.current_sequence_loss

            self.wins_in_current_signal += 1
            events.append(
                f"🟢 <b>GAIN</b> — Séquence #{self.signal_counter} | Palier {self.fib_index + 1}\n"
                f"Profit : +{profit} DHS | Capital : {self.capital} DHS"
            )

            self.fib_index = 0
            self.current_sequence_loss = 0

            if self.wins_in_current_signal >= self.target_wins:
                self.is_betting = False
                events.append(f"✅ Signal #{self.signal_counter} terminé — objectif atteint.")
        else:
            self.capital -= bet_amount
            self.current_sequence_loss += bet_amount
            self.fib_index += 1
            if actual_val != 0:
                self.target_value = actual_val

            events.append(
                f"🔴 Perte niveau {self.fib_index} | Prochaine mise : "
                f"{self.fib[self.fib_index] if self.fib_index < len(self.fib) else 'RECHARGE'} DHS"
            )

        if self.fib_index >= len(self.fib) or (self.fib_index < len(self.fib) and self.capital < self.fib[self.fib_index]):
            solde_restant = self.capital
            if solde_restant < self.actual_required_capital:
                apport = self.initial_capital - solde_restant
                self.total_real_deposits += apport
                self.capital = self.initial_capital
                events.append(f"🚨 <b>BUST</b> — Recharge de {apport} DHS nécessaire. Capital remis à {self.initial_capital} DHS.")
            else:
                events.append(f"🚨 Fin de séquence — capital auto-suffisant ({solde_restant} DHS).")

            self.is_betting = False
            self.fib_index = 0
            self.current_sequence_loss = 0
            self.streak_dozen = 0
            self.streak_column = 0

        self.p_doz, self.p_col = c_doz, c_col
        return events


# ==========================================================================
# 4. CLIENT WEBSOCKET
# ==========================================================================
engine = LiveSignalEngine(
    initial_capital=3000,
    chaos_threshold=18,
    target_wins=3,
    base_unit=None
)

last_game_id = load_last_game_id_from_csv()  # 🔧 reprend où on s'était arrêté avant un éventuel crash
if last_game_id:
    print(f"[Rattrapage] Dernier gameId connu au démarrage : {last_game_id}")


def handle_new_result(number, table_id):
    t0 = time.time()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Nouveau spin (table {table_id}) : {number}")
    events = engine.process_spin(number)
    t1 = time.time()
    for msg in events:
        send_telegram_alert(msg)
        print(msg)
    t2 = time.time()
    print(f"[TIMING] engine={t1-t0:.3f}s | telegram={t2-t1:.3f}s | total={t2-t0:.3f}s")


def process_new_results(results):
    """
    Traite tous les résultats plus récents que le dernier gameId connu,
    dans l'ordre chronologique (du plus ancien au plus récent) — pas
    seulement le dernier. Ça permet de rattraper les spins manqués pendant
    une coupure/redémarrage, tant que la coupure dure moins que la fenêtre
    de 20 résultats fournie par le WebSocket.
    """
    global last_game_id

    if last_game_id is None:
        # Premier démarrage (pas de CSV existant) : on amorce le moteur avec
        # toute la fenêtre disponible plutôt que de démarrer à l'aveugle.
        new_entries = list(reversed(results))
    else:
        idx = next((i for i, r in enumerate(results) if r.get("gameId") == last_game_id), None)
        if idx is None:
            # Le dernier gameId connu n'est plus dans la fenêtre de 20 :
            # la coupure a duré trop longtemps pour rattraper en toute sécurité.
            # On reprend juste depuis le plus récent, comme avant.
            print("[Rattrapage] ⚠️ Coupure trop longue (>20 spins) — rattrapage partiel impossible, reprise au plus récent.")
            new_entries = [results[0]] if results else []
        elif idx == 0:
            new_entries = []  # rien de nouveau depuis le dernier traitement
        else:
            new_entries = list(reversed(results[:idx]))  # du plus ancien au plus récent parmi les manqués
            if len(new_entries) > 1:
                print(f"[Rattrapage] {len(new_entries)} spin(s) manqué(s) détecté(s), traitement en cours...")

    for entry in new_entries:
        game_id = entry.get("gameId")
        if game_id is None:
            continue

        last_game_id = game_id

        # Enregistrement CSV — même point de dédoublonnage que le moteur,
        # donc chaque spin réel n'est écrit qu'une seule fois.
        t_csv0 = time.time()
        log_spin_to_csv(game_id, entry.get("result"), entry.get("color"), entry.get("time"))
        t_csv1 = time.time()
        if t_csv1 - t_csv0 > 0.1:
            print(f"[TIMING] écriture CSV lente : {t_csv1 - t_csv0:.3f}s")

        try:
            number = int(entry["result"])
        except (KeyError, ValueError, TypeError):
            continue

        handle_new_result(number, TABLE_KEY)


def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    if str(data.get("tableId")) != TABLE_KEY:
        return

    results = data.get("last20Results")
    if not results:
        return

    process_new_results(results)


def on_error(ws, error):
    print(f"[WS] Erreur : {repr(error)} (type: {type(error).__name__})")


def on_close(ws, close_status_code, close_msg):
    print(f"[WS] Connexion fermée (code={close_status_code}, msg={close_msg}). Reconnexion dans 3s...")


def on_open(ws):
    print("[WS] Connexion établie.")
    send_telegram_alert(f"🎲 Bot démarré (WebSocket). Capital : {engine.initial_capital} DHS, seuil : {engine.chaos_threshold}.")

    # 🔧 Séquence confirmée fonctionnelle : le CLIENT envoie "available" en
    # premier (pas le serveur), attend 1s, puis envoie "subscribe".
    msg1 = json.dumps({"type": "available", "casinoId": CASINO_ID})
    ws.send(msg1)
    print(f"[WS] Message 'available' envoyé : {msg1}")

    time.sleep(1)

    msg2 = json.dumps({"type": "subscribe", "currency": CURRENCY, "key": TABLE_KEY, "casinoId": CASINO_ID})
    ws.send(msg2)
    print(f"[WS] Message 'subscribe' envoyé : {msg2}")


def run_forever_with_reconnect():
    if DEBUG_TRACE:
        import logging
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')
        websocket.enableTrace(True)

    while True:
        ws = websocket.WebSocketApp(
            WS_URL,
            header=WS_HEADERS,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        run_kwargs = {"ping_interval": 30, "ping_timeout": 25, "origin": WS_ORIGIN}
        if USE_PROXY:
            run_kwargs.update({
                "http_proxy_host": PROXY_HOST,
                "http_proxy_port": PROXY_PORT,
                "http_proxy_auth": (PROXY_LOGIN, PROXY_PASSWORD),
                "proxy_type": PROXY_TYPE,
            })

        try:
            ws.run_forever(**run_kwargs)
        except Exception as e:
            print(f"[WS] Exception : {repr(e)} (type: {type(e).__name__})")

        print("[WS] Reconnexion dans 3 secondes...")
        time.sleep(3)


if __name__ == "__main__":
    if VOLUME_PATH == ".":
        print(f"⚠️ ATTENTION : RAILWAY_VOLUME_MOUNT_PATH non détecté — CSV éphémère utilisé : {os.path.abspath(CSV_FILE)}")
    else:
        print(f"✅ Volume détecté ({VOLUME_PATH}) — CSV persistant utilisé : {CSV_FILE}")
    print(f"🎲 Bot démarré | Suite Fibonacci : {engine.fib} | Capital requis : {engine.actual_required_capital} DHS")
    run_forever_with_reconnect()
