# Installatie
### Externe vereisten
- Python (Getest op 3.9)

Installeer eerst deze twee python pakketten:
```
pip install Flask sqlalchemy requests
```
Zorg er dan voor dat je een Database Driver voor sqlite geïnstalleerd heb.

## Client setup
In het client.py bestand moet je de tekst 'localhost' vervangen met het IP van je server

# Aanzetten
De server word aangezet met de twee volgende commandos:

linux: `export FLASK_APP=vooraadbeheer`  
cmd: `set FLASK_APP=vooraadbeheer`  
powershell: `$env:FLASK_APP = "vooraadbeheer"`  
Alle platformen: `python -m flask run --host=JOUW_IP`  

Open dan de url `localhost:5000` in je browser om de server kant te zien. De port kan anders zijn. Maar dan zie je dat in de tekst na het flask run commando.