import os
import asyncio
import logging
import aiohttp
import time
from logging.handlers import RotatingFileHandler
from tapo import ApiClient
from renault_api.renault_client import RenaultClient
from dotenv import load_dotenv
from datetime import datetime, time as dt_time
import json
import sys

log_handler = RotatingFileHandler(
    'ev_charger.log',
    maxBytes=2*1024*1024, 
    backupCount=3,
    encoding='utf-8'
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        log_handler,
        logging.StreamHandler(sys.stderr)
    ]
)
console_logger = logging.getLogger()
console_logger.handlers[1].setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logging.getLogger("renault_api.kamereon.models").setLevel(logging.ERROR)

class EVCharger:
    def __init__(self):
        load_dotenv()
        self.tapo_email = os.getenv('TAPO_EMAIL')
        self.tapo_password = os.getenv('TAPO_PASSWORD')
        self.smart_plug_ip = os.getenv('SMART_PLUG_IP')
        self.renault_email = os.getenv('RENAULT_EMAIL')
        self.renault_password = os.getenv('RENAULT_PASSWORD')
        self.websession = None
        self.vehicle = None
        self.last_update_id = None
        self.charging_active = False
        self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
        self.TELEGRAM_CHAT_ID1 = os.getenv("TELEGRAM_CHAT_ID1")
        self.last_known_battery_status = None

        if not all([self.tapo_email, self.tapo_password, self.smart_plug_ip,
                   self.renault_email, self.renault_password]):
            raise ValueError("Errore: alcune credenziali non sono state caricate correttamente.")

    async def send_telegram_message(self, message, force=False):
        important_keywords = ["⚠️", "✅", "🛑", "⚡", "Ricarica terminata", "cavo scollegato"]
        if not force and not any(k in message for k in important_keywords):
            # Skip messaggi non critici per evitare spam
            logger.debug(f"Messaggio Telegram ignorato per limitazione: {message}")
            return

        url = f"https://api.telegram.org/bot{self.TELEGRAM_BOT_TOKEN}/sendMessage"
        payloads = [
            {"chat_id": self.TELEGRAM_CHAT_ID, "text": message},
            {"chat_id": self.TELEGRAM_CHAT_ID1, "text": message},
        ]
        
        async with aiohttp.ClientSession() as session:
            for payload in payloads:
                for attempt in range(2):  # max 2 tentativi
                    try:
                        async with session.post(url, json=payload, timeout=10) as resp:
                            resp.raise_for_status()
                            logger.info(f"Messaggio Telegram inviato: {message[:30]}...")
                            break
                    except Exception as e:
                        logger.warning(f"Invio Telegram fallito (tentativo {attempt+1}): {e}")
                        await asyncio.sleep(1)

    async def setup(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.websession = aiohttp.ClientSession()
                client = RenaultClient(websession=self.websession, locale="it_IT")
                await client.session.login(self.renault_email, self.renault_password)
                account_list = await client.get_person()
                account_id = account_list.accounts[0].accountId
                account = await client.get_api_account(account_id)
                vehicles = await client.session.get_account_vehicles(account_id)
                vin = vehicles.vehicleLinks[0].vin
                self.vehicle = await account.get_api_vehicle(vin)
                return
            except Exception as e:
                logger.error(f"Setup fallito (tentativo {attempt+1}/{max_retries}): {e}")
                await self.close()
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
        
        raise ConnectionError("Impossibile completare il setup dopo diversi tentativi")

    async def safe_api_call(self, func, *args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await func(*args, **kwargs)
                if result is not None:
                    return result
            except Exception as e:
                logger.warning(f"Tentativo {attempt+1} fallito per {func.__name__}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        
        logger.error(f"Chiamata API {func.__name__} fallita dopo {max_retries} tentativi")
        return None

    async def get_batterystatus(self):
        status = await self.safe_api_call(self.vehicle.get_battery_status)
        if status:
            self.last_known_battery_status = status
        return status

    async def get_plug_status(self):
        status = await self.safe_api_call(self.vehicle.get_battery_status)
        if status:
            self.last_known_battery_status = status
            is_plugged = status.plugStatus != 0
            logger.info(f"Stato cavo: {'Collegato' if is_plugged else 'Scollegato'}")
            return is_plugged
        return False

    async def get_last_update_id(self):
        url = f"https://api.telegram.org/bot{self.TELEGRAM_BOT_TOKEN}/getUpdates"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    data = await resp.json()
                    if "result" in data and data["result"]:
                        return data["result"][-1]["update_id"]
        except Exception as e:
            logger.error(f"Errore nel recupero dell'ultimo update_id: {e}")
        return None

    async def wait_for_user_response(self, timeout=300):
        url = f"https://api.telegram.org/bot{self.TELEGRAM_BOT_TOKEN}/getUpdates"
        start_time = time.time()
        last_update_id = await self.get_last_update_id() or 0
        while time.time() - start_time < timeout:
            try:
                params = {"offset": last_update_id + 1, "timeout": 10}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params) as resp:
                        data = await resp.json()
                        if "result" in data and data["result"]:
                            for update in data["result"]:
                                if "message" in update and "text" in update["message"]:
                                    last_update_id = update["update_id"]
                                    return update["message"]["text"].strip().lower()
            except Exception as e:
                logger.error(f"Errore nel recupero del messaggio: {e}")
            await asyncio.sleep(2)
        return None

    async def ask_continue_charging(self):
        battery_status = await self.get_batterystatus()
        if not battery_status:
            return False
            
        battery_percentage = battery_status.batteryLevel
        await self.send_telegram_message(f"⚡ La ricarica non è necessaria. La batteria è al {battery_percentage}%. Si desidera continuare la ricarica? Rispondi 'sì','no' o inserisci la percentuale desiderata.", force=True)
        
        response = await self.wait_for_user_response()
        if response in ["sì", "si"]:
            await self.send_telegram_message("✅ Continuo la ricarica fino all'80%.", force=True)
            return 80
        elif response == "no":
            await self.send_telegram_message("🛑 Ricarica terminata. Si prega di scollegare il veicolo.", force=True)
            return False
        elif response is not None and response.isdigit() and int(response) <= 100:
            await self.send_telegram_message(f"✅ Continuo la ricarica fino al {response}%.", force=True)
            return int(response)
        else:
            await self.send_telegram_message("⏳ Nessuna risposta valida. Ricarica terminata.", force=True)
            return False

    async def start_charging(self):
        try:
            client = ApiClient(self.tapo_email, self.tapo_password)
            plug = await client.p100(self.smart_plug_ip)
            await plug.on()
            logger.info("Presa attivata, ricarica avviata.")
            self.charging_active = True
            return True
        except Exception as e:
            logger.error(f"Errore di autenticazione Tapo: {e}")
            return False

    async def stop_charging(self):
        try:
            client = ApiClient(self.tapo_email, self.tapo_password)
            plug = await client.p100(self.smart_plug_ip)
            await plug.off()
            logger.info("Presa spenta, ricarica terminata.")
            self.charging_active = False
            return True
        except Exception as e:
            logger.error(f"Errore nello spegnimento della presa: {e}")
            return False

    async def safe_sleep(self, sleep_time: float) -> bool:
        chunk_size = 60  # controlla ogni 60 secondi
        slept = 0
        while slept < sleep_time:
            chunk = min(chunk_size, sleep_time - slept)
            await asyncio.sleep(chunk)
            slept += chunk
            
            # Controlla lo stato del cavo durante lo sleep
            if not await self.get_plug_status():
                logger.warning("⚠️ Rilevato scollegamento durante lo sleep!")
                return False
        return True

    async def charge_loop(self, battery_percentage, time_estimate, target):
        first_battery_percentage = battery_percentage
        battery_status = await self.get_batterystatus()
        if not battery_status:
            logger.error("Impossibile ottenere lo stato della batteria all'inizio del ciclo.")
            return
        start_time = datetime.now().isoformat()
        battery_percentage = battery_status.batteryLevel
        charging_time_real = ((battery_status.chargingRemainingTime)*(target-battery_percentage)/(100-battery_percentage))*60
        charging_time_real_start = ((battery_status.chargingRemainingTime)*(target-battery_percentage)/(100-battery_percentage))*60
        checkpoints = list(range(((first_battery_percentage // 10) + 1) * 10, target, 10))
        initial_remaining = target-battery_percentage
        
        try:
            while battery_percentage < target:
                if battery_percentage is None:
                    logger.error("Errore nel recupero del livello batteria durante la ricarica.")
                    break

                # Controllo scollegamento con log dettagliato
                is_plugged = await self.get_plug_status()
                if not is_plugged:
                    logger.warning("⚠️ Cavo scollegato! Interruzione della ricarica.")
                    await self.send_telegram_message(f"⚠️ Cavo scollegato! Batteria al {battery_percentage}%", force=True)
                    await self.stop_charging()
                    break
                
                # Gestione checkpoint
                if checkpoints and battery_percentage >= checkpoints[0]:
                    next_checkpoint = checkpoints.pop(0)
                    logger.info(f"🔋 Batteria: {battery_percentage}% - Raggiunto checkpoint {next_checkpoint}%")
                    await self.send_telegram_message(
                        f"🔋 Batteria: {battery_percentage}% - Prossimo checkpoint {checkpoints[0] if checkpoints else target}%",
                        force=True
                    )
            
                # Fase finale con sleep frazionato
                elif not checkpoints:
                    current_remaining = target - battery_percentage
                    progress_ratio = current_remaining / initial_remaining
                    exponent = 1.5  
                    remaining_time_sec = charging_time_real * (progress_ratio ** exponent)
                    
                    min_sleep = 300  
                    max_sleep = 1800  
                    sleep_time = max(min_sleep, min(remaining_time_sec, max_sleep))
                    
                    logger.info(f"Ultimo sleep progressivo: {sleep_time//60} min {sleep_time%60} sec")
                    await self.send_telegram_message(
                        f"⏳ Progresso: {battery_percentage}% → {target}% | "
                        f"Prossimo controllo in {sleep_time//60} min {round(sleep_time%60)} sec",
                        force=False  # evita spam in questa fase
                    )
                    
                    if not await self.safe_sleep(sleep_time):
                        # Cavo scollegato durante lo sleep, interrompo
                        break
                else:
                    estimated_time_sec = (charging_time_real*(checkpoints[0]-battery_percentage))/(target-battery_percentage)
                    logger.info(f"Dormo {estimated_time_sec // 60} min fino a circa {checkpoints[0]}%")
                    if not await self.safe_sleep(estimated_time_sec):
                        break
                
                # Aggiorno stato batteria
                battery_status = await self.get_batterystatus()
                if battery_status is None:
                    break
                new_battery_percentage = battery_status.batteryLevel
            
                # Adatto la stima tempo ricarica
                if new_battery_percentage <= battery_percentage:
                    charging_time_real *= 0.9
                
                battery_percentage = new_battery_percentage
                charging_time_real = ((battery_status.chargingRemainingTime)*(target-battery_percentage)/(100-battery_percentage))*60

                if battery_percentage >= target:
                    break
        finally:
            end_time = datetime.now().isoformat()
            end_status = await self.get_batterystatus()
            if not end_status:
                logger.warning("Usando ultimo stato batteria noto per salvataggio")
                end_status = self.last_known_battery_status
                return
            start_battery_capacity = (first_battery_percentage*27)/100
            end_battery_capacity = (end_status.batteryLevel*27)/100
            energy_consumed = round(end_battery_capacity - start_battery_capacity, 2)
            charging_duration_hours = (charging_time_real) / 3600
            energy_expected = (end_status.batteryLevel - first_battery_percentage)/ 100
            energy_measured = charging_duration_hours * 1.35
            battery_health = ((energy_measured / energy_expected)/27) * 100 if energy_expected > 0 else None
            cockpit = await self.vehicle.get_cockpit()
            total_mileage_value = cockpit.totalMileage
            
            data = {
                "start_time": start_time,
                "end_time": end_time,
                "start_battery_level": first_battery_percentage,
                "end_battery_level": end_status.batteryLevel,
                "start_battery_capacity": start_battery_capacity,
                "end_battery_capacity": end_battery_capacity,
                "EnergyConsumed": energy_consumed,
                "battery_autonomy": end_status.batteryAutonomy,
                "charging_duration_hours": charging_duration_hours,
                "energy_expected": energy_expected,
                "energy_measured": energy_measured,
                "battery_health_estimate": round(battery_health, 2) if battery_health else None,
                "charging_status": end_status.chargingStatus,
                "total_mileage": total_mileage_value
            }
            file_path = "charging_data.json"
            try:
                if os.path.exists(file_path):
                    with open(file_path, 'r') as f:
                        all_data = json.load(f)
                else:
                    all_data = []

                all_data.append(data)

                with open(file_path, 'w') as f:
                    json.dump(all_data, f, indent=4)
                logger.info("Dati di ricarica salvati in charging_data.json")
            except Exception as e:
                logger.error(f"Errore nel salvataggio JSON: {e}")

            await self.stop_charging()
            logger.info("Livello batteria target raggiunto. Ricarica completata.")
            await self.send_telegram_message(f"✅ Livello batteria {target}% raggiunto. Ricarica completata.", force=True)

    async def run_charging_cycle(self):
        logger.info("Avvio del ciclo di ricarica.")
        battery_status = await self.get_batterystatus()
        if not battery_status:
            logger.error("Impossibile recuperare il livello della batteria. Interruzione del ciclo.")
            return

        battery_percentage = battery_status.batteryLevel

        if battery_percentage >= 50:
            target = await self.ask_continue_charging()
            if not target:
                await self.stop_charging()
                await self.send_telegram_message("Si prega di scollegare il veicolo. Attendo 7 ore prima di riprovare.", force=True)
                await asyncio.sleep(3600*7)
                return
            time_estimate = round(((target - battery_percentage) * 612) / 60)
            await self.start_charging()
            await self.send_telegram_message(f"Ricarica in corso. Batteria attuale: {battery_percentage}% - Tempo stimato per {target}%: {time_estimate} min", force=True)
            await self.charge_loop(battery_percentage, time_estimate, target)

        elif battery_percentage < 50:
            target = 80
            time_estimate = round(((target - battery_percentage) * 612) / 60)
            await self.send_telegram_message(f"Batteria bassa ({battery_percentage}%). Avvio ricarica fino all'80%: {time_estimate} min", force=True)
            if await self.start_charging():
                await self.charge_loop(battery_percentage, time_estimate, target)
            else:
                logger.error("Impossibile avviare la ricarica.")
        else:
            await self.stop_charging()
            await self.send_telegram_message(f"Batteria al {battery_percentage}%, ricarica non necessaria.", force=True)

    async def monitor_plug_status(self):
        logger.info("Monitoraggio del cavo di ricarica avviato.")
        while True:
            is_plugged = await self.get_plug_status()
            if is_plugged:
                logger.info("Cavo collegato!")
                await self.send_telegram_message("⚡ Cavo collegato! Controllo lo stato della ricarica...", force=True)
                await self.run_charging_cycle()
            else:
                logger.info("Cavo scollegato rilevato nel monitoraggio.")
            await asyncio.sleep(900)  # Controllo ogni 15 minuti

    async def close(self):
        if self.websession:
            await self.websession.close()

if __name__ == "__main__":
    async def main():
        try:
            charger = EVCharger()
            await charger.setup()
            remaining_call = await charger.vehicle.get_battery_status()
            battery = remaining_call.batteryLevel
            remaining = round(remaining_call.chargingRemainingTime*(80-battery)/(100-battery))*60
            logger.info(f"Tempo stimato di ricarica restante: {remaining} secondi")
            await charger.monitor_plug_status()
        finally:
            await charger.close()
            logger.info("Sessione terminata")

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Errore durante l'esecuzione dello script: {e}")

