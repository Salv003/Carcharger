import os
import asyncio
import logging
import aiohttp
import time
from tapo import ApiClient
from renault_api.renault_client import RenaultClient
from dotenv import load_dotenv

TELEGRAM_BOT_TOKEN = "8135262155:AAFXAIMrIqDFcaYkFIKY4fmszCTEuUQPNZw"
TELEGRAM_CHAT_ID = "647755647"
TELEGRAM_CHAT_ID1 = "1041256243"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

class EVCharger:
    def __init__(self):
        self.tapo_email = os.getenv('TAPO_EMAIL')
        self.tapo_password = os.getenv('TAPO_PASSWORD')
        self.smart_plug_ip = os.getenv('SMART_PLUG_IP')
        self.renault_email = os.getenv('RENAULT_EMAIL')
        self.renault_password = os.getenv('RENAULT_PASSWORD')
        if not all([self.tapo_email, self.tapo_password, self.smart_plug_ip, self.renault_email, self.renault_password]):
            raise ValueError("Errore: alcune credenziali non sono state caricate correttamente.")

    async def send_telegram_message(self, message):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payloads = [
            {"chat_id": TELEGRAM_CHAT_ID, "text": message},
            '''{"chat_id": TELEGRAM_CHAT_ID1, "text": message}''',
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

    async def get_battery_percentage(self):
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
                return battery_status.batteryLevel
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
                is_plugged = plug_status.plugStatus != 1
                logger.info(f"Stato del cavo: {'Collegato' if is_plugged else 'Scollegato'}")
                return is_plugged
        except aiohttp.ClientError as e:
            logger.error(f"Errore nel recupero dello stato del cavo: {e}")
            return False

    async def wait_for_user_response(self, timeout=60):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        start_time = time.time()
        last_update_id = await self.get_last_update_id() or 0
        while time.time() - start_time < timeout:
            try:
                params = {"offset": last_update_id + 1, "timeout": 10}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params) as resp:
                        response = await resp.json()
                        if "result" in response and response["result"]:
                            for update in response["result"]:
                                if "message" in update and "text" in update["message"]:
                                    last_update_id = update["update_id"]
                                    return update["message"]["text"].strip().lower()
            except aiohttp.ClientError as e:
                logger.error(f"Errore nel recupero del messaggio: {e}")
            await asyncio.sleep(2)
        return None

    async def ask_continue_charging(self):
        await self.send_telegram_message("⚡ La ricarica non è necessaria. Vuoi continuare la ricarica? Rispondi 'sì' o 'no'.")
        response = await self.wait_for_user_response()
        if response == str.casefold("si") or response == str.casefold("sì"):
            await self.send_telegram_message("✅ Continuo la ricarica.")
            return True
        elif response == str.casefold("no"):
            await self.send_telegram_message(" Interrompo la ricarica.")
            return False
        else:
            await self.send_telegram_message("⏳ Tempo scaduto, interrompo la ricarica.")
            return False

    async def start_charging(self):
        try:
            client = ApiClient(self.tapo_email, self.tapo_password)
            plug = await asyncio.to_thread(client.p100, self.smart_plug_ip)
            await asyncio.to_thread(plug.on)
            logger.info("Presa attivata, ricarica avviata.")
            return True
        except Exception as e:
            logger.error(f"Errore di autenticazione Tapo: {e}")
            return False
        except Exception as e:
            logger.error(f"Errore Tapo: {e}")
            return False
        except Exception as e:
            logger.error(f"Errore generico nell'accensione della presa: {e}")
            return False

    async def stop_charging(self):
        try:
            client = ApiClient(self.tapo_email, self.tapo_password)
            plug = await asyncio.to_thread(client.p100, self.smart_plug_ip)
            await asyncio.to_thread(plug.off)
            logger.info("Presa spenta, ricarica terminata.")
            return True
        except Exception as e:
            logger.error(f"Errore di autenticazione Tapo: {e}")
            return False
        except Exception as e:
            logger.error(f"Errore Tapo: {e}")
            return False
        except Exception as e:
            logger.error(f"Errore generico nello spegnimento della presa: {e}")
            return False

    async def run_charging_cycle(self):
        try:
            logger.info("Avvio del ciclo di ricarica.")
            battery_percentage = await self.get_battery_percentage()
            if battery_percentage is None:
                logger.error("Impossibile recuperare il livello della batteria. Interruzione del ciclo.")
                return
            time_estimate = (80 - battery_percentage) * 612
            if battery_percentage >= 50 and await self.ask_continue_charging():
                await self.start_charging()
                logger.info(f"Ricarica in corso. Batteria attuale: {battery_percentage}%")
                await self.send_telegram_message(f"Ricarica in corso. Batteria attuale: {battery_percentage}%. Tempo di Ricarica previsto per l'80%: {time_estimate // 60} min")
                await self.charge_loop(battery_percentage, time_estimate)
            elif battery_percentage < 50:
                logger.info(f"Batteria bassa ({battery_percentage}%). Avvio ricarica.")
                await self.send_telegram_message(f"Batteria bassa ({battery_percentage}%). Avvio ricarica.")
                if await self.start_charging():
                    await self.charge_loop(battery_percentage, time_estimate)
                else :
                    logger.error("Impossibile avviare la ricarica.")
            else:
                logger.info(f"Batteria al {battery_percentage}%, ricarica non necessaria.")
                await self.stop_charging()
                await self.send_telegram_message(f"Batteria al {battery_percentage}%, ricarica non necessaria.")
        except Exception as e:
            logger.error(f"Errore critico nel ciclo di ricarica: {e}")

    async def charge_loop(self, battery_percentage, time_estimate):
        last_battery_percentage = battery_percentage
        while battery_percentage < 80:
            battery_percentage = await self.get_battery_percentage()
            if battery_percentage is None:
                logger.error("Errore nel recupero del livello batteria durante la ricarica.")
                break
            if not await self.get_plug_status():
                logger.warning("⚠️ Cavo scollegato! Interruzione della ricarica.")
                await self.send_telegram_message(f"⚠️ Cavo scollegato! Batteria al {battery_percentage}%")
                await self.stop_charging()
                break
            logger.info(f" Batteria: {battery_percentage}%. Tempo stimato per 80%: {time_estimate // 60} min")
            if abs(battery_percentage - last_battery_percentage) >= 1 or time_estimate < 900:
                await self.send_telegram_message(f" Batteria: {battery_percentage}%. Tempo stimato per 80%: {time_estimate // 60} min")
                last_battery_percentage = battery_percentage
            sleep_time = min(900, time_estimate // (80 - battery_percentage))
            await asyncio.sleep(sleep_time)
        await self.stop_charging()
        logger.info("Livello batteria target raggiunto. Ricarica completata.")
        await self.send_telegram_message("Livello batteria target raggiunto. Ricarica completata.")

    async def monitor_plug_status(self):
        logger.info("Monitoraggio del cavo di ricarica avviato.")
        first_run = True
        while True:
            if await self.get_plug_status():
                logger.info("Cavo collegato! Avvio della ricarica.")
                if first_run:
                    await self.send_telegram_message("⚡ Cavo collegato! Controllo lo stato della ricarica...")
                    first_run = False
                await self.run_charging_cycle()
            else:
                logger.warning("⚠️ Cavo scollegato!")
                first_run = True
            await asyncio.sleep(900)

    async def get_last_update_id(self):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    if "result" in data and data["result"]:
                        return data["result"][-1]["update_id"]
        except aiohttp.ClientError as e:
            logger.error(f"Errore nel recupero dell'ultimo update_id: {e}")
        except Exception as e:
            logger.error(f"Errore generico nel recupero dell'ultimo update_id: {e}")
        return None

async def main():
    charger = EVCharger()
    await charger.monitor_plug_status()

if __name__ == "__main__":
    try:
        if os.name == "nt":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Errore durante l'esecuzione dello script: {e}")
