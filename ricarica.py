import os
import asyncio
import logging
import aiohttp
import time
import requests
from tapo import ApiClient
from renault_api.renault_client import RenaultClient
from dotenv import load_dotenv


load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_CHAT_ID1 = os.getenv("TELEGRAM_CHAT_ID1")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("renault_api.kamereon.models").setLevel(logging.ERROR)


class EVCharger:
    def __init__(self):
        self.tapo_email = os.getenv('TAPO_EMAIL')
        self.tapo_password = os.getenv('TAPO_PASSWORD')
        self.smart_plug_ip = os.getenv('SMART_PLUG_IP')
        self.renault_email = os.getenv('RENAULT_EMAIL')
        self.renault_password = os.getenv('RENAULT_PASSWORD')
        if not all([self.tapo_email, self.tapo_password, self.smart_plug_ip, self.renault_email, self.renault_password]):
            raise ValueError("Errore: alcune credenziali non sono state caricate correttamente.")
        self.last_update_id = None  # Per la gestione dei messaggi Telegram

    async def send_telegram_message(self, message):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payloads = [
            {"chat_id": TELEGRAM_CHAT_ID, "text": message},
            {"chat_id": TELEGRAM_CHAT_ID1, "text": message},
        ]
        try:
            async with aiohttp.ClientSession() as session:
                tasks = [session.post(url, json=payload) for payload in payloads]
                responses = await asyncio.gather(*tasks)
                for response in responses:
                    response.raise_for_status()
        except aiohttp.ClientError as e:
            logger.error(f"Errore nell'invio del messaggio Telegram: {e}")
        except Exception as e:
            logger.error(f"Errore generico nell'invio del messaggio Telegram: {e}")

    async def get_batterystatus(self):
        try:
            async with aiohttp.ClientSession() as websession:
                client = RenaultClient(websession=websession, locale="it_IT")
                await client.session.login(self.renault_email, self.renault_password)
                account_list = await client.get_person()
                account_id = account_list.accounts[0].accountId
                account = await client.get_api_account(account_id)
                vehicles = await client.session.get_account_vehicles(account_id)
                vin = vehicles.vehicleLinks[0].vin
                vehicle = await account.get_api_vehicle(vin)
                battery_status = await vehicle.get_battery_status()
                battery_level = battery_status
                return battery_level
        except aiohttp.ClientError as e:
            logger.error(f"Errore nel recupero della batteria: {e}")
            return None

    async def get_plug_status(self):
        try:
            async with aiohttp.ClientSession() as websession:
                client = RenaultClient(websession=websession, locale="it_IT")
                await client.session.login(self.renault_email, self.renault_password)
                account_list = await client.get_person()
                account_id = account_list.accounts[0].accountId
                account = await client.get_api_account(account_id)
                vehicles = await client.session.get_account_vehicles(account_id)
                vin = vehicles.vehicleLinks[0].vin
                vehicle = await account.get_api_vehicle(vin)
                plug_status = await vehicle.get_battery_status()
                is_plugged = plug_status.plugStatus != 0
                logger.info(f"Stato del cavo: {'Collegato' if is_plugged else 'Scollegato'} {plug_status.plugStatus}")
                return is_plugged
        except aiohttp.ClientError as e:
            logger.error(f"Errore nel recupero dello stato del cavo: {e}")
            return False

    async def get_last_update_id(self):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
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
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
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
        battery_percentage = await self.get_batterystatus()
        battery_percentage = battery_percentage.batteryLevel
        target = 0
        await self.send_telegram_message(f"⚡ La ricarica non è necessaria. La batteria è al {battery_percentage} %. Si desidera continuare la ricarica? Rispondi 'sì','no' o inserisci la cifra della percentuale desiderata.")
        response = await self.wait_for_user_response()
        if response in ["sì", "si"]:
            await self.send_telegram_message("✅ Continuo la ricarica fino all' 80 %.")
            target = 80
            return target
        elif response == "no":
            await self.send_telegram_message("🛑 Ricarica terminata. Si prega di scollegare il veicolo.")
            return False
        elif isinstance(response, int):
            await self.send_telegram_message(f"✅ Continuo la ricarica fino all' {response} %.")
            target = response
            return target
        else:
            await self.send_telegram_message("⏳ Nessuna risposta valida. Ricarica terminata.")
            return False

    async def start_charging(self):
        try:
            client = ApiClient(self.tapo_email, self.tapo_password)
            plug = await client.p100(self.smart_plug_ip)
            await plug.on()
            logger.info("Presa attivata, ricarica avviata.")
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
            return True
        except Exception as e:
            logger.error(f"Errore nello spegnimento della presa: {e}")
            return False

    async def charge_loop(self, battery_percentage, time_estimate, target):
        last_battery_percentage = battery_percentage
        battery_percentage_call = await self.get_batterystatus()
        battery_percentage = battery_percentage_call.batteryLevel
        while battery_percentage < target:
            if battery_percentage is None:
                logger.error("Errore nel recupero del livello batteria durante la ricarica.")
                break

            if not await self.get_plug_status():
                logger.warning("⚠️ Cavo scollegato! Interruzione della ricarica.")
                await self.send_telegram_message(f"⚠️ Cavo scollegato! Batteria al {battery_percentage}%")
                await self.stop_charging()
                break
            time_estimate =  (80-battery_percentage)*612                
            if abs(battery_percentage - last_battery_percentage) >= 10 or time_estimate < 900:
                logger.info(f"🔋 Batteria: {battery_percentage}% - Tempo stimato per 80%: {time_estimate // 60} min")
                await self.send_telegram_message(f"🔋 Batteria: {battery_percentage}% - Tempo stimato per 80%: {time_estimate // 60} min")
                last_battery_percentage = battery_percentage

            sleep_time = 900
            await asyncio.sleep(sleep_time)
            battery_percentage = battery_percentage_call.batteryLevel
        await self.stop_charging()
        logger.info("Livello batteria target raggiunto. Ricarica completata.")
        await self.send_telegram_message("✅ Livello batteria target raggiunto. Ricarica completata.")

    async def run_charging_cycle(self):
        logger.info("Avvio del ciclo di ricarica.")
        battery_percentage = await self.get_batterystatus()
        battery_percentage = battery_percentage.batteryLevel
        if battery_percentage is None:
            logger.error("Impossibile recuperare il livello della batteria. Interruzione del ciclo.")
            return

        time_estimate_call = await self.get_batterystatus()
        time_estimate = time_estimate_call.chargingRemainingTime
        if battery_percentage >= 50:
            # Quando la batteria è ≥ 50, chiedi se continuare la ricarica
            target = await self.ask_continue_charging()
            if target:
                await self.start_charging()
                await self.send_telegram_message(f"Ricarica in corso. Batteria attuale: {battery_percentage}% - Tempo stimato per l'{target}%: {(target*time_estimate)/100} min")
                await self.charge_loop(battery_percentage, time_estimate, 80)
            else:
                await self.stop_charging()
                await self.send_telegram_message("Si prega di scollegare il veicolo.")
                await asyncio.sleep((3600)*7)
        elif battery_percentage < 50:
            await self.send_telegram_message(f"Batteria bassa ({battery_percentage}%). Avvio ricarica. Per l' 80%: {time_estimate//60} min")
            if await self.start_charging():
                await self.charge_loop(battery_percentage, time_estimate,80)
            else:
                logger.error("Impossibile avviare la ricarica.")
        else:
            await self.stop_charging()
            await self.send_telegram_message(f"Batteria al {battery_percentage}%, ricarica non necessaria.")

    async def monitor_plug_status(self):
        logger.info("Monitoraggio del cavo di ricarica avviato.")
        
        while True:
            if await self.get_plug_status():
                logger.info("Cavo collegato!")
                await self.send_telegram_message("⚡ Cavo collegato! Controllo lo stato della ricarica...")
                await self.run_charging_cycle()
            else:
                logger.warning("⚠️ Cavo scollegato!")
            await asyncio.sleep(900)  # Controllo ogni 15 minuti

if __name__ == "__main__":
    async def main():
        charger = EVCharger()
        await charger.monitor_plug_status()
        
    try:
        import sys
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Errore durante l'esecuzione dello script: {e}")
