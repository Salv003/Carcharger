import json
import random
from datetime import datetime, timedelta
import numpy as np

# === COSTANTI ===
KM_PER_KWH = 7.41
MAX_SOC_PER_HOUR = 5
MIN_SOC_START = 20
MAX_SOC_START = 40
MIN_SOC_END = 70
MAX_SOC_END = 90
FULL_CAPACITY_KWH = 26

# === GENERA SESSIONE ===
def generate_valid_session(start_time, mileage, health=90, reverse=False):
    start_battery_level = random.randint(MIN_SOC_START, MAX_SOC_START)

    low = MIN_SOC_END - start_battery_level
    high = min(40, 100 - start_battery_level)
    if low > high:
        low = high
    delta = random.randint(max(5, low), high)
    duration_hours = round(max(delta / MAX_SOC_PER_HOUR, 1.0) + random.uniform(0.1, 0.5), 3)

    end_battery_level = start_battery_level + int(duration_hours * MAX_SOC_PER_HOUR)
    end_battery_level = min(end_battery_level, 100)
    end_time = start_time + timedelta(hours=duration_hours)

    health = round(np.clip(np.random.normal(health, 2), 75, 100), 2)
    full_capacity = FULL_CAPACITY_KWH * (health / 100)

    start_capacity = round(full_capacity * start_battery_level / 100 + random.uniform(-0.1, 0.1), 2)
    end_capacity = round(full_capacity * end_battery_level / 100 + random.uniform(-0.1, 0.1), 2)

    energy_consumed = max(0.1, round(end_capacity - start_capacity, 2))
    energy_expected = round(energy_consumed, 2)
    energy_measured = round(energy_expected * random.uniform(0.95, 1.1), 2)

    autonomy = int(np.clip((end_battery_level / 100) * 230 + random.uniform(-10, 10), 100, 270))

    km = round(energy_consumed * KM_PER_KWH, 1)
    mileage = mileage - km if reverse else mileage + km

    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "start_battery_level": start_battery_level,
        "end_battery_level": end_battery_level,
        "start_battery_capacity": round(start_capacity, 2),
        "end_battery_capacity": round(end_capacity, 2),
        "EnergyConsumed": round(energy_consumed, 2),
        "battery_autonomy": autonomy,
        "charging_duration_hours": round(duration_hours, 3),
        "energy_expected": energy_expected,
        "energy_measured": energy_measured,
        "battery_health_estimate": health,
        "charging_status": float(random.choice([0.0, 1.0])),
        "charging_time": int(duration_hours * 3600),
        "total_mileage": round(mileage, 1),
    }, mileage, end_time

# === GENERA SESSIONI PRIMA DI UNA DATA ===
def generate_sessions_before(base_data, from_date, n_sessions):
    base_data = sorted(base_data, key=lambda x: x["start_time"])
    first = base_data[0]
    mileage = first.get("total_mileage", 0)
    health = first.get("battery_health_estimate", 90)
    current_time = datetime.fromisoformat(first["start_time"]) - timedelta(hours=2)

    sessions = []
    for _ in range(n_sessions):
        if current_time < from_date:
            break
        
        # Introduco probabilità di saltare la ricarica in questo intervallo (es. 30%)
        if random.random() < 0.3:  # 30% chance di non generare sessione
            # Salto 1 o 2 giorni indietro senza generare sessioni
            current_time -= timedelta(days=random.randint(1, 2))
            continue

        session, mileage, end_time = generate_valid_session(current_time, mileage, health, reverse=True)
        sessions.append(session)
        current_time -= timedelta(hours=random.uniform(2, 6) + session["charging_duration_hours"])
    return sessions[::-1]  # inverti cronologicamente


# === GENERA SESSIONI DOPO UNA DATA ===
def generate_sessions_after(base_data, until_date, n_sessions):
    base_data = sorted(base_data, key=lambda x: x["start_time"])
    last = base_data[-1]
    mileage = last.get("total_mileage", 0)
    health = last.get("battery_health_estimate", 90)
    current_time = datetime.fromisoformat(last["end_time"]) + timedelta(hours=2)

    sessions = []
    for _ in range(n_sessions):
        if current_time > until_date:
            break
        
        # Introduco probabilità di saltare la ricarica in questo intervallo (es. 30%)
        if random.random() < 0.3:  # 30% chance di non generare sessione
            # Salto 1 o 2 giorni avanti senza generare sessioni
            current_time += timedelta(days=random.randint(1, 2))
            continue

        session, mileage, current_time = generate_valid_session(current_time, mileage, health)
        sessions.append(session)
        current_time += timedelta(hours=random.uniform(2, 6))
    return sessions


# === ESECUZIONE ===
if __name__ == "__main__":
    with open("Progetto SmartEVCharger/charging_data.json", "r") as f:
        base_data = json.load(f)

    start_date = datetime(2025, 4, 1)
    end_date = datetime(2025, 8, 31)

    before_sessions = generate_sessions_before(base_data, start_date, n_sessions=100)
    after_sessions = generate_sessions_after(base_data, end_date, n_sessions=80)

    all_sessions = before_sessions + base_data + after_sessions
    all_sessions.sort(key=lambda x: x["start_time"])

    with open("ricariche_dacia_spring.json", "w") as f:
        json.dump(all_sessions, f, indent=4)

    print("✅ File generato: ricariche_dacia_spring.json")
