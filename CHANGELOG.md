# Journal des versions

## 1.2.0 — 2026-10-29

### Intégrations maison natives
- **py-intercom** : nouvelle action *Intercom* (talkback maintenu, basculer, ouvrir, fermer, couper tous les bus) via l'API locale du client (`http://127.0.0.1:5080`). Au maintien, le micro s'ouvre à l'appui et se coupe au relâchement. La touche est bleue micro ouvert, grise quand le client est désarmé ou injoignable.
- **orchestream** : nouvelle action *Overlay orchestream* (Twizz, Sondage, Speedizz : afficher, masquer, basculer). L'état est suivi en direct par le flux `/api/overlay/events` : un overlay basculé depuis l'interface web d'orchestream allume ou éteint la touche. Un refus (par ex. « Aucun Speedizz à afficher ») est écrit dans le Moniteur.
- **Tally32** : nouvelle action *Reconnecter les tallys* et retour d'état natif. Chaque tally est surveillé toutes les 3 s (`GET /status`). La touche est verte si tous sont connectés à vMix, rouge avec le nom des tallys en défaut.
- **Réglages → Intégrations** : adresses de py-intercom, d'orchestream et des tallys, avec leur état en direct.
- Bibliothèque : boutons prêts *Talk Régie / Plateau / vMix*, *Couper tous les talkbacks*, *Twizz*, *Sondage*, *Speedizz*, *Tallys*.
- Une intégration ne travaille que si une touche l'utilise.

### Conversion automatique des configurations Companion
- À l'import Companion (ou à l'ouverture d'une configuration 1.x), les touches *Intercom* (raccourcis VICREO maintenus), les interrupteurs show/hide d'orchestream et le bouton *Check Tally* (pings + GET + variables) sont convertis vers ces intégrations natives.

### Limite connue
- orchestream ne mémorise pas l'état de Twizz et du Sondage côté serveur : au démarrage, la touche garde un liseré gris jusqu'au premier changement. Speedizz est connu tout de suite.

## 1.1.0 — 2026-10-29

### Take, appui long et sécurité
- **Take** : une seule touche au lieu de deux. 1er appui, l'input passe en preview (vert) ; 2e appui, la transition choisie l'envoie à l'antenne (rouge). C'est le type proposé par défaut quand on glisse des inputs.
- **Appui court / appui long** (½ s) : une deuxième série d'actions par touche, avec une jauge qui se remplit en haut de la touche.
- **Sécurité** : la touche doit être maintenue ½ s pour se déclencher. Boutons prêts *FTB sécurisé* et *Arrêt du stream*.

### Mode Live verrouillé
- En Live, l'édition est figée (inspecteur, collage, suppression, dépôts) ; seul **▶ Tester** reste actif.

### Recherche et diagnostic
- **Ctrl+F** : retrouve une touche sur toutes les pages par son texte, son input, son action ou sa page.
- **Diagnostic Stream Deck**, comme dans MixPanel : écrans (numéros, couleurs, luminosité), touches (chaque appui se coche), performances (latence vMix, lecture de l'état, affichage d'une touche). Pendant le diagnostic, les touches ne déclenchent rien.

### Exe, barre des tâches et démarrage automatique
- **Exe Windows unique** (`LiveDeck-x.y.z-win64.exe`) avec icône et informations de version, `hidapi.dll` embarquée.
- Fermer la fenêtre réduit LiveDeck près de l'horloge : les Stream Decks continuent de fonctionner.
- **Démarrage automatique** à l'ouverture de session (réduit), option *démarrer en Live*.
- Une seule instance : relancer LiveDeck ramène la fenêtre au premier plan.
- Configuration rangée dans `%APPDATA%\LiveDeck` (reprise automatique de l'ancien dossier `data/`).

## 1.0.0 — 2026-10-29

Première version : une alternative légère à Bitfocus Companion pour piloter vMix avec des Stream Decks.

- Stream Deck à l'écran, à l'image du deck branché, avec rendu identique sur l'écran et sur les touches.
- Inputs vMix en direct : double-clic pour remplir une rangée, glisser-déposer pour créer des touches Program, Preview, Overlay ou Son.
- Actions vMix (preview, program, transitions, overlays, son, enregistrement, stream, fonction libre), raccourcis clavier (maintenus ou non), requêtes HTTP (coller une commande curl), pages et pauses.
- Retours d'état déduits de l'action, sans réglage : rouge à l'antenne, vert en preview, jaune en overlay…
- Import des configurations Bitfocus Companion (`.companionconfig`).
- Annuler / rétablir, copier / coller, envoyer une touche vers une autre page, Moniteur des actions et des erreurs.
- Interface PySide6 au même langage visuel que MixPanel.
