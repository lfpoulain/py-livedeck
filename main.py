"""Lance LiveDeck.  Usage : python main.py [--tray] [--selftest]

--tray      démarre réduit près de l'horloge (utilisé par le démarrage automatique)
--selftest  démarre, vérifie que tout se charge, écrit selftest.txt dans le dossier des données et quitte
"""
from livedeck.app import main

if __name__ == "__main__":
    main()
