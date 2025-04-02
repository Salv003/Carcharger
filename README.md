# EVCharger - Gestione Intelligente della Ricarica di Veicoli Elettrici

Questo progetto Python fornisce un sistema intelligente per la gestione della ricarica di veicoli elettrici (EV). Utilizza una combinazione di API per monitorare lo stato della batteria del veicolo, controllare una presa intelligente e comunicare con l'utente tramite Telegram.

## Funzionalità Principali

* **Monitoraggio della Batteria:** Recupera in tempo reale il livello della batteria del veicolo utilizzando l'API Renault.
* **Controllo della Presa Intelligente:** Accende e spegne una presa intelligente Tapo per avviare e interrompere la ricarica del veicolo.
* **Comunicazione Telegram:** Invia messaggi informativi all'utente tramite Telegram, inclusi aggiornamenti sullo stato della batteria e promemoria per scollegare il cavo.
* **Logica di Ricarica Intelligente:** Gestisce il ciclo di ricarica in base al livello della batteria e alle preferenze dell'utente, con la possibilità di interrompere la ricarica se il cavo viene scollegato.
* **Gestione degli Errori:** Implementa una robusta gestione degli errori per garantire l'affidabilità del sistema.

## Requisiti

* Python 3.7 o superiore
* Librerie Python:
    * `aiohttp`
    * `tapo`
    * `renault_api`
    * `python-dotenv`
    * `requests`
* Account Telegram con un bot creato
* Account Renault con veicolo elettrico associato
* Presa intelligente Tapo

## Installazione

1.  Clona questo repository:

    ```bash
    git clone [https://docs.github.com/en/repositories/creating-and-managing-repositories/deleting-a-repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/deleting-a-repository)
    ```

2.  Installa le dipendenze:

    ```bash
    pip install -r requirements.txt
    ```

3.  Crea un file `.env` nella directory del progetto e inserisci le tue credenziali:

    ```plaintext
    TAPO_EMAIL=tuo_email_tapo
    TAPO_PASSWORD=tua_password_tapo
    SMART_PLUG_IP=indirizzo_ip_presa_tapo
    RENAULT_EMAIL=tuo_email_renault
    RENAULT_PASSWORD=tua_password_renault
    ```

4.  Sostituisci `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` nel codice con i tuoi valori.

## Utilizzo

Esegui lo script Python:

```bash
python ricarica.py
