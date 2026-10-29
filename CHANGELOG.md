# Journal des versions

## 1.3.0 — 2026-10-29

### Fiabilité en direct
- **Talkback jamais bloqué** : le relâché part toujours vers le bus et les touches de l'appui, même si tu changes de page, modifies ou effaces la touche pendant que tu la maintiens. Un « off » perdu est renvoyé jusqu'à ce que py-intercom confirme le micro fermé.
- **Tout est relâché** à la fermeture de LiveDeck (raccourcis maintenus, interrupteurs, micros py-intercom), et ce que tenait un Stream Deck quand il est débranché, sans couper le talkback des autres decks.
- **Maintien sur toutes les touches** : un raccourci maintenu ou un talkback marche aussi sur une touche avec appui long, avec sécurité (à partir de ½ s) ou en interrupteur (1er appui ouvre, 2e ferme). Un interrupteur verrouillé est relâché si sa touche est effacée ou changée ; un interrupteur sécurisé ne bascule qu'après ½ s de maintien.
- **Configuration protégée** : écriture atomique, une sauvegarde par jour et avant chaque import dans `%APPDATA%\LiveDeck\backups` (20 gardées). Une configuration abîmée est mise de côté et LiveDeck démarre quand même, en te disant où retrouver tes sauvegardes ; une configuration seulement verrouillée (OneDrive, antivirus) n'est jamais écrasée.
- Un vMix figé est détecté (plus de réponse depuis 3,5 s) et LiveDeck se reconnecte tout seul ; une erreur dans un fil ne l'arrête plus, et tout est écrit dans le journal (`livedeck.log`, tournant).
- Les Stream Decks gardent leur page après les Réglages, le Diagnostic ou une coupure USB. Nouveaux modèles : Stream Deck + XL, Studio et Pedal ; les touches tactiles du Neo changent de page.

### Take et vMix
- **Take plus clair** : touche verte = en preview, prête à prendre ; rouge = à l'antenne (le rouge l'emporte toujours) ; liseré vert sous le rouge = aussi en preview. Sur un input déjà à l'antenne, Take ne fait rien.
- **Double appui rapide** : l'état est mis à jour dès l'envoi de la commande, sans attendre la lecture de vMix, donc deux appuis rapprochés font bien preview puis antenne, Mix 2 à 4 compris.
- **2e appui sur une Preview** : option pour envoyer à l'antenne un input déjà en preview, avec la transition de ton choix (aussi proposée à l'import Companion). Par défaut, rien ne change.
- Liste des transitions complète (effets inversés, boutons de transition 1-4 réglés dans vMix, Stingers) ; FTB démarrer / arrêter n'envoie la commande que si nécessaire.
- Un input vide ou introuvable n'envoie plus rien et le Moniteur le dit ; un refus de vMix cite la commande refusée. Les valeurs avec espaces (`SetText`…) sont envoyées telles quelles.

### Intégrations
- **Diagnostic py-intercom** : chaque échec a son explication en français (client fermé, « Contrôle HTTP » non coché, mauvaise IP, pare-feu, proxy, client désarmé, bus absent…). Bouton **Tester** pour chaque intégration dans Réglages → Intégrations (sur les adresses que tu viens de saisir) et dans l'inspecteur.
- **Bus découverts** : la liste des bus vient du client py-intercom ; la touche affiche HORS LIGNE, DÉSARMÉ, BUS ABSENT ou PERMANENT (bus « Toujours actif »).
- **Raccourci de secours** : un raccourci clavier peut prendre le relais si py-intercom ne répond pas (si le client était seulement lent, le relâché lui envoie aussi « off »). Il est gardé à la conversion d'une config Companion.
- **Proxy** : les adresses locales (127.0.0.1, réseau privé, `.local`) ne passent plus jamais par le proxy de Windows.
- **orchestream** : liste d'overlays libre (Réglages → Intégrations), et les overlays vus dans le flux en direct sont proposés automatiquement. Tally32 : message clair si le firmware est trop ancien.
- L'import Companion ne convertit vers py-intercom / orchestream / Tally32 que si tu coches « Convertir vers les intégrations natives ». Avec « Ajouter les pages », les réglages trouvés (orchestream, overlays, tallys) complètent les tiens.

### Nouvelles actions
- **Message OSC** (QLab, Reaper, X32, TouchDesigner…) et **Commande TCP / UDP** (texte avec `\r \n \xNN` ou hexadécimal), dans la nouvelle catégorie *Réseau*.
- Retour d'état **Valeur d'une API JSON** : lit une URL, compare une valeur (égal, supérieur, contient…) et peut l'afficher sur la touche (`Spectateurs\n{value}`).

### Texte dynamique
- Le texte d'une touche peut contenir des variables mises à jour en direct : `$(vmix:program_title)`, `$(vmix:remaining:Magneto)`, `$(vmix:text:Bandeau:Headline)`, `$(time)`… Menu **Variables…** dans l'inspecteur. Les variables Companion sont converties à l'import.

### Interface
- **Colonne de droite redimensionnable**, sans défilement horizontal ; les colonnes se règlent à la souris et LiveDeck s'en souvient.
- **Touche vide** : toutes les familles d'actions (vidéo, audio, production, intégrations, clavier, web, réseau, deck), avec recherche et catégories. Une intégration pas encore réglée est marquée « à configurer ».
- **Prochain appui** affiché sous le titre de la touche ; Take, Program, Preview, Overlay et Son demandent leur input dès la création ; **Convertir en Take** (touche, rangée ou page).
- **Statut des intégrations** : une pastille par intégration utilisée dans l'en-tête (clic = réglages), statut et Tester dans l'inspecteur.
- **Moniteur** : compteur d'erreurs et d'alertes, filtre (erreurs, actions envoyées), Copier, Effacer, accès au fichier journal ; les alertes s'affichent aussi en bandeau.
- **Remplir la rangée…** (clic droit) : place une série d'inputs en Take, Preview, Overlay…; **export / import d'une page** seule (menu de l'onglet ou **Fichier**).
- Mode Live : vue de la touche en lecture seule (ce qu'elle fait, prochain appui, problèmes). Survoler une touche du deck à l'écran affiche ce qu'elle fait.
- Menu **Fichier** (import, export, Diagnostic, aide), raccourcis **F1** (raccourcis et légende des couleurs), **F2** (texte), **Entrée** (tester), **Ctrl+Pg↑ / Pg↓** (page).

### Compatibilité
- Le talkback demande **py-intercom compilé le 29/10/2026 ou plus récent**, avec « Contrôle HTTP » coché dans la zone Parler (et « Depuis le réseau » si LiveDeck tourne sur un autre PC).
- Ta configuration est reprise telle quelle (les transitions « FadeToBlack » deviennent des touches FTB).

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
