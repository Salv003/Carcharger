import os
import asyncio
import logging
import aiohttp
import time
import requests
from tapo import ApiClient
from renault_api.renault_client import RenaultClient
from dotenv import load_dotenv
from datetime import datetime
import json


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
        self.websession = None
        self.vehicle = None
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

    async def setup(self):
        self.websession = aiohttp.ClientSession()
        client = RenaultClient(websession=self.websession, locale="it_IT")
        await client.session.login(self.renault_email, self.renault_password)
        account_list = await client.get_person()
        account_id = account_list.accounts[0].accountId
        account = await client.get_api_account(account_id)
        vehicles = await client.session.get_account_vehicles(account_id)
        vin = vehicles.vehicleLinks[0].vin
        self.vehicle = await account.get_api_vehicle(vin)

    async def get_batterystatus(self):
        try:
                battery_status = await self.vehicle.get_battery_status()
                battery_level = battery_status
                return battery_level
        except aiohttp.ClientError as e:
            logger.error(f"Errore nel recupero della batteria: {e}")
            return None

    async def get_plug_status(self):
        try:
                plug_status = await self.vehicle.get_battery_status()
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
        elif int(response)<=100:
            await self.send_telegram_message(f"✅ Continuo la ricarica fino all' {response} %.")
            target = int(response)
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
        first_battery_percentage = battery_percentage
        battery_percentage_call = await self.get_batterystatus()
        start_time = datetime.now().isoformat()
        battery_percentage = battery_percentage_call.batteryLevel
        charging_time = (target-battery_percentage)*612
        charging_time_real = ((battery_percentage_call.chargingRemainingTime)*(target-battery_percentage)/(100-battery_percentage))*60
        checkpoints = list(range(((first_battery_percentage // 10) + 1) * 10, target, 10))
        while battery_percentage < target:
            if battery_percentage is None:
                logger.error("Errore nel recupero del livello batteria durante la ricarica.")
                break

            if not await self.get_plug_status():
                logger.warning("⚠️ Cavo scollegato! Interruzione della ricarica.")
                await self.send_telegram_message(f"⚠️ Cavo scollegato! Batteria al {battery_percentage}%")
                await self.stop_charging()
                break
            if checkpoints and battery_percentage >= checkpoints[0]:
                next_checkpoint = checkpoints.pop(0)
                logger.info(f"🔋 Batteria: {battery_percentage}% - Raggiunto checkpoint {next_checkpoint}%")
                await self.send_telegram_message(
                    f"🔋 Batteria: {battery_percentage}% - Prossimo checkpoint {checkpoints[0] if checkpoints else target}%"
                )

            if not checkpoints:
                remaining_time_sec = charging_time_real
                logger.info(f"Ultimo sleep per raggiungere il target: {remaining_time_sec // 60} min")
                await self.send_telegram_message(f"Tempo rimanente per l' {target} % : circa {remaining_time_sec // 60} min ")
                await asyncio.sleep(remaining_time_sec)                

            estimated_time_sec = charging_time_real*checkpoints[0]/target
            logger.info(f"Dormo {estimated_time_sec // 60} min fino a circa {checkpoints[0]}%")
            await asyncio.sleep(estimated_time_sec)
            battery_percentage_call = await self.get_batterystatus()
            battery_percentage = battery_percentage_call.batteryLevel
            charging_time_real = ((battery_percentage_call.chargingRemainingTime)*(target-battery_percentage)/(100-battery_percentage))*60
        end_time = datetime.now().isoformat()
        end_status = await self.get_batterystatus()
        start_battery_capacity = (first_battery_percentage*27)/100
        end_battery_capacity = (end_status.batteryLevel*27)/100
        energyconsumed = round(end_battery_capacity-start_battery_capacity, 2)
        charging_duration_hours = (charging_time_real) / 3600
        energy_expected = (end_status.batteryLevel - first_battery_percentage) * 27 / 100
        energy_measured = charging_duration_hours * 1.35
        battery_health = (energy_measured / energy_expected) * 100 if energy_expected > 0 else None
        cockpit = await self.vehicle.get_cockpit()
        total_mileage_value = cockpit.totalMileage
        
        data = {
            "start_time": start_time,
            "end_time": end_time,
            "start_battery_level": first_battery_percentage,
            "end_battery_level": end_status.batteryLevel,
            "start_battery-capacity":start_battery_capacity,
            "end_battery_capacity": end_battery_capacity,
            "EnergyConsumed": energyconsumed,
            "battery_autonomy": end_status.batteryAutonomy,
            "charging_duration_hours": charging_duration_hours,
            "energy_expected": energy_expected,
            "energy_measured": energy_measured,
            "battery_health_estimate": round(battery_health, 2) if battery_health else None,
            "charging_status": end_status.chargingStatus,
            "charging_time": charging_time,
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
        await self.send_telegram_message(f"✅ Livello batteria {target} raggiunto. Ricarica completata.")

    async def run_charging_cycle(self):
        logger.info("Avvio del ciclo di ricarica.")
        battery_percentage = await self.get_batterystatus()
        battery_percentage = battery_percentage.batteryLevel
        if battery_percentage is None:
            logger.error("Impossibile recuperare il livello della batteria. Interruzione del ciclo.")
            return

        if battery_percentage >= 50:
            target = await self.ask_continue_charging()
            time_estimate = round(((target-battery_percentage)*612)/60)
            if target:
                await self.start_charging()
                await self.send_telegram_message(f"Ricarica in corso. Batteria attuale: {battery_percentage}% - Tempo stimato per l'{target}%: {time_estimate} min")
                await self.charge_loop(battery_percentage, time_estimate, target)
            else:
                await self.stop_charging()
                await self.send_telegram_message("Si prega di scollegare il veicolo. Attendo 7 ore prima di riprovare")
                await asyncio.sleep(3600*7)
        elif battery_percentage < 50:
            await self.send_telegram_message(f"Batteria bassa ({battery_percentage}%). Avvio ricarica. Per l' {target} %: {time_estimate} min")
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
            await asyncio.sleep(900)  

if __name__ == "__main__":
    async def main():
        charger = EVCharger()
        await charger.setup()
        remaining_call = await charger.vehicle.get_battery_status()
        battery = remaining_call.batteryLevel
        remaining = round(remaining_call.chargingRemainingTime*(80-battery)/(100-battery))*60
        print(remaining)
        await charger.monitor_plug_status()
        
    try:
        import sys
        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Errore durante l'esecuzione dello script: {e}")
