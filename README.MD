# Installatie
### Externe vereisten
- Python (Getest op 3.9)

Installeer eerst deze drie python pakketten:
```
pip install Flask sqlalchemy requests
```
Zorg er dan voor dat je een Database Driver voor sqlite geïnstalleerd heb.

# Aanzetten
De server word aangezet met het volgende commando `python voorraadbeheer.py --host=<IP>`  

Open dan de url `localhost:5000` in je browser om de server kant te zien. De port kan anders zijn. Maar dan zie je dat in de tekst na het flask run commando.