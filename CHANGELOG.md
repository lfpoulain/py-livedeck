# Journal des versions

## 1.6.2 — 2026-11-06

Version de fiabilité, issue d'un audit complet du code. Chaque correction est couverte par un test
qui échoue sur la version précédente.

### Direct
- **Une action maintenue ne peut plus rester bloquée.** Une seule erreur au relâché d'une touche
  (page sans nom, configuration remplacée au même moment…) laissait le raccourci maintenu enfoncé — pour
  un bouton intercom, le micro restait ouvert jusqu'à la fermeture de LiveDeck. Le relâché des actions
  maintenues est désormais garanti, et chaque touche est relâchée indépendamment des autres.
- **Une configuration contenant une valeur vide ne repart plus de zéro.** Un seul « null » dans le
  fichier le faisait classer comme corrompu, et LiveDeck démarrait avec une configuration vierge.
- **Un retour d'état défaillant n'affecte plus que son bouton.** Il interrompait la mise à jour de tous
  les textes dynamiques (comptes à rebours, durée d'enregistrement, niveaux).
- Une preview attendue que vMix refuse n'est plus affichée indéfiniment.

### Loupedeck
- **Plus d'écritures vers un appareil en panne.** Après une erreur USB, chaque écriture pouvait bloquer
  5 secondes, et la reconnexion attendait la fin : plusieurs minutes d'appareil muet.
- **L'interface ne se fige plus** pendant l'ouverture d'un appareil qui ne répond pas (1,3 s toutes les
  2 secondes).
- **Les appuis ne sont plus perdus** quand l'appareil annonce un numéro de série différent de celui de
  l'USB (cas probable sur les Razer).
- **Un octet parasite ne bloque plus les vrais appuis.** La lecture attendait jusqu'à 255 octets qui
  pouvaient ne jamais venir — y compris un relâché, laissant la touche enfoncée.
- L'action part avant la vibration, et le port est libéré immédiatement en cas de panne.
- Toucher la molette en tenant une vignette ne relâche plus la vignette trop tôt.
- Après « Identifier » ou le diagnostic, tout est redessiné : une diode de tally pouvait rester allumée.
- Conversion des images 10 à 17 fois plus rapide, au bit près identique.

### Édition
- Changer de page oublie la molette sélectionnée : « Suppr » effaçait celle de la nouvelle page.
- Le clic droit sur une molette ouvre le menu (il plantait).
- Un input déposé sur une molette ne l'empêche plus de tourner.
- Coller une molette sur une touche ne laisse plus de rotation impossible.

### Divers
- Fermer les Réglages arrête leur minuteur : chaque ouverture en laissait un de plus, actif jusqu'à la
  fermeture de LiveDeck.
- Une application absente n'est plus recherchée dans l'audio Windows à chaque dessin.
- Une réponse HTTP mal formée ne laisse plus le voyant d'un appareil figé au vert.

## 1.6.1 — 2026-11-05

### Loupedeck : plus rien ne se déclenche sans qu'on l'ait demandé
- **Aucun bouton n'a de rôle par défaut.** `Fn G` et `Fn D` changeaient de page tant qu'ils étaient
  libres : un comportement qu'on découvrait en appuyant par accident. Pour naviguer, pose
  « Page suivante » sur le bouton de ton choix.
- **« Suivre le deck » est décoché par défaut**, et ton choix est mémorisé. Coché, le moindre appui
  sur l'appareil déplace la sélection de l'éditeur : pratique pour câbler, intenable en direct.
- **La vibration devient une option**, par appareil, dans Réglages → Decks. Décochée, l'appareil ne
  vibre plus du tout.

### Molettes
- **Choisir une action pour une molette demande où la poser** : à l'appui, à la rotation ⟳, à la
  rotation ⟲, ou les deux. Les choix sont les mêmes pour les six encodeurs et la molette centrale ;
  seul celui qui convient le mieux à l'action est conseillé. Avant, l'action partait d'office sur
  l'appui.
- **Un bouton ne garde plus d'action de rotation.** Poser un réglage de molette sur un bouton rond y
  laissait une rotation qui ne pouvait jamais partir. Elle devient l'appui, ou disparaît si le bouton
  a déjà une action. Les configurations existantes se réparent toutes seules au chargement.

### Affichage
- **La vue dessine la grille de l'appareil, pas celle de la page.** Une page 5×3 affichée sur un
  Loupedeck CT montrait une 5e colonne qui n'existe pas. Si une page déborde du pavé, le pied de
  l'appareil l'indique en rouge.
- Le contrôle suivi ou sélectionné est entouré de blanc, comme une touche.

### Divers
- Un double-clic dans la bibliothèque visait parfois une autre case que la molette sélectionnée.
- Le mode « preview + program », qui occupe deux rangées, ne plante plus sur une molette.
- Un échec d'ouverture passager d'un Loupedeck ne s'affiche plus en erreur ; un échec qui dure est
  signalé une seule fois, avec la marche à suivre.
- CI : l'auto-test vérifie l'application packagée au lieu de lancer l'installateur, qui attendait
  indéfiniment une boîte de dialogue sur un runner sans session interactive.

## 1.6.0 — 2026-11-05

### Loupedeck
- **Les Loupedeck fonctionnent à côté de tes Stream Decks**, avec les mêmes pages, les mêmes actions et les
  mêmes retours d'état : Loupedeck CT, CT V1, Live, Live S, Razer Stream Controller et Stream Controller X.
  Rien à régler : branche l'appareil, il apparaît dans Réglages comme un deck de plus. Les deux familles
  peuvent afficher des pages différentes en même temps.
- **Pavé tactile** : les 12 vignettes sont des touches de page ordinaires. Un doigt qui glisse hors d'une
  vignette la relâche quand même (sinon un bus intercom maintenu restait ouvert).
- **Molettes** : rotation dans les deux sens et appui, chacun avec sa liste d'actions. Un **pas** évite
  d'inonder vMix sur une rotation rapide, sans perdre les crans restants. Dans une action, `$(delta)` vaut le
  nombre de crans avec son signe et `$(delta_abs)` sans.
- **Molette centrale du CT** : son écran rond affiche le libellé et le retour d'état ; la toucher équivaut à
  l'enfoncer.
- **Bandeaux latéraux** : trois zones affichables par bandeau, qui se câblent comme des touches.
- **Boutons physiques** : les 8 boutons ronds (dont la **diode RGB** suit le retour d'état), ainsi que Home,
  Annuler, Clavier, Entrée, Enregistrer, Fn gauche/droite et A à E. Sans action affectée, Fn gauche et Fn
  droite changent de page.
- **Vibration** à l'appui, réglable par contrôle (une dalle tactile ne donne sinon aucun retour).
- Les contrôles hors grille s'éditent dans le même inspecteur, sous la vue du deck.
- Le logiciel Loupedeck officiel doit être fermé : il garde le port série de l'appareil.

### Molettes : réglages au cran
- Nouvelle action **Régler au cran** : volume d'un input, du Master, du casque ou d'un bus A-G, avance /
  retour dans une vidéo, parcours des inputs en preview, photos / liste / PowerPoint, compte à rebours.
- La jauge et la valeur s'affichent sur la molette et suivent les changements faits dans vMix.
- Une rotation rapide ne perd aucun cran : vMix ne renvoie son état que quelques fois par seconde, LiveDeck
  repart donc de la dernière valeur envoyée.
- Un réglage au cran se pose d'un seul côté : c'est le sens de rotation qui décide de monter ou de baisser.
- Catégorie **Molette** dans la bibliothèque (Volume Master, Volume d'un input, Parcourir la preview,
  Avance / retour vidéo…), proposée en premier quand on configure une molette.

### Windows
- **Son** : volume et coupure du son de Windows, du micro par défaut, ou d'une seule application (Spotify,
  navigateur, Teams…). Touche rouge quand c'est coupé, jauge du volume.
- **Lecture multimédia**, **ouvrir un programme / fichier / site**, **fenêtre au premier plan** (avant un
  raccourci clavier), **commande Windows**, **taper un texte**, **molette de souris**, **verrouiller la session**.
- Aucune dépendance ajoutée : le son passe directement par l'API audio de Windows.

## 1.5.0 — 2026-11-05

### Verrou de direct
- **Verrouiller** (bouton de l'en-tête, Ctrl+L, ou menu de l'icône près de l'horloge) : plus d'édition ni de clic sur
  les touches de l'écran, pour éviter les fausse manips pendant un direct. Les Stream Decks physiques restent actifs.
  Déverrouiller demande une confirmation ; le verrou est conservé si LiveDeck est relancé.

### Installateur
- **LiveDeck s'installe** : la release fournit `LiveDeck-x.y.z-win64-setup.exe` à la place de l'exe seul. Installation pour ton compte
  (sans droits administrateur), raccourci dans le menu Démarrer, raccourci Bureau en option, désinstallation dans
  Paramètres → Applications.
- **Mise à jour** : lance le nouvel installateur par-dessus l'ancien (il propose de fermer LiveDeck s'il tourne).
  Ta configuration (`%APPDATA%\LiveDeck`) n'est jamais touchée, même à la désinstallation.
- Démarrage plus rapide : l'app n'est plus décompressée dans un dossier temporaire à chaque lancement.

## 1.4.0 — 2026-11-04

### orchestream-v2
- **Compatible orchestream-v2** : LiveDeck lit l'état des overlays au démarrage (`/api/overlay/state`) et suit
  chaque changement par le canal temps réel `overlay` d'orchestream-v2. Une touche est donc juste dès le
  lancement, même si Twizz ou le Sondage étaient déjà à l'antenne : plus de liseré gris en attendant.
- Les refus d'orchestream-v2 sont affichés tels quels dans le Moniteur (« Aucun Twizz à afficher : lancez une
  partie d'abord. »).
- orchestream-v2 refuse la régie par son adresse publique (tunnel, passerelle) : **Tester** l'explique et indique
  d'utiliser l'adresse locale (par ex. `http://192.168.1.20:5174`).
- L'ancienne version d'orchestream reste reconnue automatiquement, sans rien régler.

## 1.3.1 — 2026-11-02

### Correctif
- **Molette** : dans la colonne de droite (et dans les Réglages), la molette fait toujours défiler la colonne. Elle ne change plus la valeur d'une liste, d'un nombre ou d'un curseur qui passe sous la souris ; ces champs se règlent au clic ou au clavier.

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
