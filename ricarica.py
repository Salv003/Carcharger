import os
import asyncio
import logging
from tapo import ApiClient
from renault_api.renault_client import RenaultClient
from dotenv import load_dotenv
import aiohttp
import requests

TELEGRAM_BOT_TOKEN = "8135262155:AAFXAIMrIqDFcaYkFIKY4fmszCTEuUQPNZw"
TELEGRAM_CHAT_ID = "647755647"

# Configura il logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Carica le variabili d'ambiente
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
        
    async def send_telegram_message(self,message):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, json=payload)

    async def get_battery_percentage(self):
        try:
            async with aiohttp.ClientSession() as websession:
                client = RenaultClient(websession=websession, locale="it_IT")
                await client.session.login(self.renault_email, self.renault_password)

                account_list = await client.get_person()
                account_id = account_list.accounts[0].accountId
                account = await client.get_api_account(account_id)

                vehicles = await client.session.get_account_vehicles(account_id)
                vin = vehicles.vehicleLinks[0].vin  # Identificativo del veicolo

                vehicle = await account.get_api_vehicle(vin)
                battery_status = await vehicle.get_battery_status()
                battery_level = battery_status.batteryLevel
                logger.info(f"Livello batteria attuale: {battery_level}%")
                return battery_level
        except Exception as e:
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
                is_plugged = plug_status.plugStatus == 1 
                logger.info(f"Stato del cavo: {'Collegato' if is_plugged else 'Scollegato'}")                 
                return is_plugged
        except Exception as e:
            logger.error(f"Errore nel recupero dello stato del cavo: {e}")
            return False

    async def start_charging(self):
        try:
            client = ApiClient(self.tapo_email, self.tapo_password)
            plug = await client.p100(self.smart_plug_ip)
            await plug.on()
            logger.info("Presa attivata, ricarica avviata.")
            return True
        except Exception as e:
            logger.error(f"Errore nell'accensione della presa: {e}")
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

    async def run_charging_cycle(self):
        try:
            logger.info("Avvio del ciclo di ricarica.")
            battery_percentage = await self.get_battery_percentage()
            time = (80-battery_percentage+2)*720
            if battery_percentage is None:
                logger.error("Impossibile recuperare il livello della batteria. Interruzione del ciclo.")
                return

            if battery_percentage < 50:
                logger.info(f"Batteria bassa ({battery_percentage}%). Avvio ricarica.")
                await self.send_telegram_message(f"Batteria bassa ({battery_percentage}%). Avvio ricarica.")
                if not await self.start_charging():
                    logger.error("Impossibile avviare la ricarica.")
                    return

                while battery_percentage < 80:
                    battery_percentage = await self.get_battery_percentage()
                    if battery_percentage is None:
                        logger.error("Errore nel recupero del livello batteria durante la ricarica.")
                        break
                    is_plugged = await self.get_plug_status()
                    if not is_plugged:
                        logger.warning("⚠️ Cavo scollegato! Interruzine della ricarica.")
                        await self.send_telegram_message(f"⚠️ Cavo scollegato! Interruzine della ricarica. Batteria al {battery_percentage}")
                        await self.stop_charging()
                    logger.info(f"Ricarica in corso. Batteria attuale: {battery_percentage}%")
                    await self.send_telegram_message(f"Ricarica in corso. Batteria attuale: {battery_percentage}% Tempo di Ricarica previsto per l'80%: {time}")
                    await asyncio.sleep(900)

                await self.stop_charging()
                logger.info("Livello batteria target raggiunto. Ricarica completata.")
                await self.send_telegram_message("Livello batteria target raggiunto. Ricarica completata.")
            else:
                logger.info(f"Batteria al {battery_percentage}%, ricarica non necessaria.")
                await self.stop_charging()
                await self.send_telegram_message(f"Batteria al {battery_percentage}%, ricarica non necessaria.")
        except Exception as e:
            logger.error(f"Errore critico nel ciclo di ricarica: {e}")

    async def monitor_plug_status(self):
        logger.info("Monitoraggio del cavo di ricarica avviato.")
        while True:
            is_plugged = await self.get_plug_status() 
            if is_plugged:
                logger.info("Cavo collegato! Avvio della ricarica.")
                await self.send_telegram_message("⚡ Cavo collegato! Controllo lo stato della ricarica...")
                await self.run_charging_cycle()
            elif not is_plugged:
                logger.warning("⚠️ Cavo scollegato!")
                await self.stop_charging()
            await asyncio.sleep(900)

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
