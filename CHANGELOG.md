# Journal des versions

## 1.13.0 — 2026-11-23

### Les outils, chacun à sa place
- **Nouvel onglet « Outils »** dans le panneau de gauche, à côté de *Inputs* et *Boutons* : py-intercom,
  orchestream, py-multiview, MixPanel, DMAIX, les caméras ONVIF et Tally32 ont chacun leur section, au lieu d'un
  seul bloc *Intégrations*. Chaque section montre l'**état de l'outil** (pastille verte, rouge ou grise et son
  texte) et ses touches rangées par **rubrique**, telles que l'outil les annonce (« Scènes », « Effets »,
  « Jeux », « Overlay », « Flux »…). Un menu n'affiche qu'un seul outil ; la recherche porte sur tout.
- **Les vignettes sont les vraies touches** : texte, couleur et état de l'outil, mis à jour en direct.
- **Touche vide** : une pastille par outil dans « Que doit faire cette touche ? », avec sa couleur.
- L'onglet *Boutons* ne garde que les familles d'actions (Vidéo, Audio, Windows, Clavier, Web…).

### Réglages : une page par outil
- **Réglages → Intégrations** n'est plus une longue page où tout se suit : une liste à gauche (py-intercom,
  orchestream, py-multiview, MixPanel, DMAIX, caméras ONVIF, Tally32) avec la **pastille d'état** de chacun, et à
  droite les réglages du seul outil choisi.
- La pastille d'un outil dans la barre d'état et le bouton **Réglages…** de l'inspecteur ouvrent directement sa
  page.

### Texte et couleurs d'une touche d'app
- Une touche décrite par un outil prend par défaut son texte, sa couleur et son état de l'outil. L'action
  **Touche d'une app** a maintenant deux réglages :
  - **Texte affiché** : celui de l'app (titre et état), le titre seul, *mon texte puis l'état de l'app*, ou
    *mon texte seul* (le texte de la touche, variables comprises) ;
  - **Couleurs** : celles de l'app, ou **celles de la touche** (Apparence), avec un liseré à la couleur de l'app
    quand elle est allumée.
- Une touche qui ne peut pas servir reste toujours grisée avec sa raison, et HORS LIGNE s'affiche toujours.
- Pour une couleur à toi selon l'état : **＋ Personnaliser** → *Touche d'une app* (allumée, éteinte,
  indisponible, injoignable). Ce retour passe par-dessus celui de l'outil.

### Corrigé
- **Gel de quelques secondes à l'enregistrement des Réglages** (et à leur fermeture) : LiveDeck coupait le flux
  en direct des apps (orchestream, py-multiview, MixPanel, DMAIX, py-intercom) en attendant, dans l'interface, que
  la lecture en cours se termine, soit une seconde ou plus par app. La connexion est maintenant coupée sans rien
  attendre.
- **Flèches en carré vide sur les touches** : les boutons *Page suivante*, *Page précédente*, *Lecture / pause* et
  *Piste suivante* de la bibliothèque affichaient un carré à la place de ▶, ◀ et ❚, absents de la police des
  touches. Ils utilisent maintenant ►, ◄ et ‖. Les touches déjà posées sont corrigées à l'affichage, sans rien
  refaire, comme tout texte où tu as tapé ces signes.

## 1.12.0 — 2026-11-23

### Caméras ONVIF
- **Nouvelle intégration ONVIF** : LiveDeck pilote directement tes caméras PTZ, sans passer par vMix. Réglages →
  Intégrations, section **ONVIF** : une caméra par ligne (`Nom = adresse`, port `80` par défaut), **Utilisateur**
  et **Mot de passe** communs, **Tester** (dit si le PTZ répond et compte les presets). Des identifiants propres
  à une caméra s'écrivent dans son adresse : `Nom = utilisateur:motdepasse@adresse`.
- **Action « Caméra ONVIF (PTZ) »** : gauche, droite, haut, bas, les quatre diagonales, zoom + et −, avec leur
  vitesse. Le mouvement dure **tant que la touche est enfoncée** et s'arrête au relâché ; zoom et panoramique
  se pilotent en même temps avec deux touches. Aussi : *Arrêter le mouvement*, *Position d'origine*, *Aller à
  un preset* et *Enregistrer la position dans un preset* (par son nom ou son numéro, la liste est lue sur la
  caméra).
- La bibliothèque (*Intégrations*) propose, pour chaque caméra, ses mouvements, l'origine et **une touche par
  preset** trouvé sur la caméra.
- La touche est bleue tant que la caméra bouge, grisée avec **HORS LIGNE** si la caméra ne répond pas. Barre
  d'état : un voyant ONVIF quand une touche l'utilise. Nouveau retour d'état *Caméra ONVIF*.
- Comme pour un talkback, un mouvement maintenu est toujours arrêté : changement de page, touche modifiée,
  deck débranché ou fermeture de LiveDeck.
- Aucune dépendance ajoutée. L'identification part des deux façons qu'une caméra peut demander (jeton
  WS-Security daté à l'heure de la caméra, et Digest HTTP) ; une caméra qui annonce ses services sur une
  ancienne adresse IP est quand même jointe sur l'adresse réglée.

### PTZ dans vMix
- **Action « Caméra PTZ (vMix) »** pour la caméra réglée dans un input vMix : mouvements, zoom et mise au point
  **tant que la touche est enfoncée** (avec la vitesse), arrêt, position d'origine, mise au point automatique
  ou manuelle, et les **PTZ virtuels** : aller à la position d'un input, l'enregistrer, en créer un.
- Bibliothèque (*Vidéo*) : PTZ ◀ ▶ ▲ ▼, zoom + / − et *PTZ position*.
- Toutes les fonctions PTZ de l'API sont proposées dans *Fonction vMix* (`PTZMoveUp`, `PTZZoomIn`,
  `PTZFocusAuto`, `PTZUpdateVirtualInput`…).

## 1.11.0 — 2026-11-23

### Touches décrites par chaque app (API Stream Deck commune)
- **py-multiview décrit ses touches** comme orchestream : réception, chaque flux, le son de chaque flux, émission
  OMT, retour audio, volume, fenêtre de retour, routes. Elles sont dans la bibliothèque (*Intégrations*) avec
  leur texte, leur couleur et leur état, mis à jour en direct par le flux `/api/deck/events` de py-multiview.
  Il faut une version de py-multiview qui expose `/api/deck` ; **Tester** le dit sinon.
- **Une action « Touche d'une app »** remplace l'action *Orchestream* : on choisit l'app (orchestream,
  py-multiview…) puis la touche dans la liste qu'elle envoie. Le retour d'état *Touche orchestream* devient
  *Touche d'une app*. Toute app qui expose le même contrat `/api/deck` se branche de la même façon.
- **Jeton API facultatif** pour py-multiview : envoyé seulement s'il est rempli. La touche n'affiche JETON ? que si
  l'app en demande un, JETON REFUSÉ si celui réglé est refusé. orchestream exige toujours le sien.
- Réglages → Intégrations : py-multiview a son champ **Jeton API (facultatif)** et son **Tester**. Sans adresse, py-multiview est cherché sur ce poste (`127.0.0.1:5090`).
- Messages d'erreur et Moniteur au nom de l'app (« py-multiview · Émission OMT : aucun écran assigné »).
- **MixPanel** (console X-Touch pour vMix) se pilote aussi : état de vMix et de la X-Touch, chaque scène, chaque
  macro, ducking, page A / B, verrouillage et mode de la X-Touch, avec leur texte, leur couleur et leur état en
  direct. Réglages → Intégrations : section **MixPanel** (adresse, **Jeton API facultatif**, **Tester**). Sans
  adresse, MixPanel est cherché sur ce poste (`127.0.0.1:5085`) ; une IP seule suffit, le port `5085` est ajouté.
  **Tester** reconnaît MixPanel (nom et version) et, si rien ne répond, rappelle d'ouvrir l'API dans *MixPanel ›
  Réglages › Pilotage à distance*. Il faut une version de MixPanel qui expose `/api/deck`.
- Barre d'état : un voyant MixPanel quand une touche l'utilise.

- **py-intercom décrit ses touches** : une par bus (Régie, Plateau, vMix : bleu, ambre, violet, rouge « live » quand
  le bus transmet), *Tout couper* et l'état de la liaison. Choisis-les dans la bibliothèque (*Intégrations*) ou
  l'action *Touche d'une app* (app *py-intercom*) ; elles suivent le flux `/api/deck/events` du poste et se
  grisent avec la raison (hors ligne, bus en mode Permanent). Adresse `127.0.0.1:5080` par défaut, **jeton API
  facultatif** (champ dans Réglages → Intégrations).
- **La parole (action *Intercom*) passe elle aussi par `/api/deck`** : py-intercom ne sert plus que cette API, les
  anciennes routes `/ptt/<bus>/…` et `/status` n'existent plus (404). LiveDeck lit l'état sur
  `GET /api/deck/state` et commande `POST /api/deck/commands/ptt.on | ptt.off | ptt.toggle | ptt.allOff`.
  Il faut un client py-intercom qui sert `/api/deck` ; **Tester** le dit sinon. Rien à refaire sur les touches
  existantes, mais elles ne marchent plus avec un ancien client.
- Aucun en-tête `Origin` ni `Referer` n'est envoyé (py-intercom les refuse comme venant d'un navigateur).
- **DMAIX** (contrôleur DMX) se pilote aussi : une touche par scène et par effet de sa bibliothèque, Stop effets,
  Blackout, l'état de la sortie DMX et de la scène en cours, avec leur texte, leur couleur et leur état en direct.
  Réglages → Intégrations : section **DMAIX** (adresse, port `6174` ajouté s'il manque, **Jeton API facultatif**,
  **Tester**). Il faut une version de DMAIX qui expose `/api/deck`.

### Retiré
- L'ancienne intégration py-multiview (`GET /status`, `POST /<objet>/<verbe>`) : l'action *py-multiview*,
  *py-multiview : volume du retour*, le retour d'état *py-multiview* et leurs presets. Toutes ses touches passent
  par `/api/deck`.
- Aucune conversion : les anciennes touches *Orchestream* (`orchestream.key`) et *py-multiview* d'une
  configuration existante ne sont plus reconnues ; à reposer depuis la bibliothèque.
- La conversion des anciennes touches orchestream (*Overlay orchestream*, *orchestream : commande régie*) à
  l'ouverture d'une configuration ou d'une page : elles ont été converties par la 1.10.0. orchestream n'a plus les
  raccourcis publics `/api/overlay/<widget>/show|hide` ; l'import Companion « intégrations natives » continue de
  transformer un interrupteur show / hide vers un overlay en touche `overlay.toggle:<widget>`.

## 1.10.0 — 2026-11-23

### Touches orchestream décrites par le serveur
- **Une seule action « Orchestream »** : la touche se choisit dans la liste qu'orchestream-v2 envoie, groupée par
  rubrique (« Jeux », « Overlay », « Studio », « État »…) et avec une recherche. Plus aucune liste de commandes en
  dur : chaque raccourci Studio, widget, élément d'overlay, sondage préparé, quiz… a sa touche, et une nouveauté
  d'orchestream apparaît sans mettre LiveDeck à jour. La bibliothèque propose aussi toutes ces touches.
- **Texte, couleur et état viennent d'orchestream** : titre et ce qui est en cours (« Fermer Q3 / Culture G »,
  « Tout remercier / 3 à remercier »), couleur vive quand la touche est allumée, grisée avec la raison quand elle
  ne peut pas servir. Une touche supprimée dans orchestream s'affiche INTROUVABLE ; un jeton révoqué, JETON REFUSÉ.
- **En direct** : les touches suivent le flux `/api/deck/events` (changement visible dans la seconde, sans
  interroger le serveur), avec reconnexion automatique ; si le flux ne s'ouvre pas, l'état est relu chaque seconde.
- Nouveau retour d'état **Touche orchestream** (allumée, éteinte, indisponible, injoignable) pour colorer n'importe
  quelle touche selon orchestream.
- Le jeton API est maintenant nécessaire pour toutes les touches orchestream ; il part toujours dans l'en-tête.

### Retiré
- L'ancienne action *Overlay orchestream* (raccourcis publics `/api/overlay/<widget>/show|hide` sans jeton), la
  prise en charge d'orchestream v1, la liste des overlays dans les Réglages et l'action *orchestream : commande
  régie*. Les touches existantes sont **converties à l'ouverture** : un overlay devient `overlay.toggle:<widget>`
  (ou `overlay.show:` / `overlay.hide:` selon son mode), une commande devient sa touche (`game.advance`,
  `twizz.start:<quiz>`). Une page importée ou un import Companion avec « intégrations natives » sont convertis aussi.

## 1.9.1 — 2026-11-12

### Corrections
- **Une seule instance, pour de bon** : deux lancements rapprochés (double-clic, démarrage de Windows + clic sur le
  raccourci) pouvaient ouvrir deux LiveDeck qui se disputaient les Stream Decks. Un verrou est maintenant pris dès
  le début du démarrage ; le second lancement réveille la première fenêtre, même si elle démarre encore.
- **La croix réduit au lieu de quitter** : si l'icône près de l'horloge n'était pas encore prête (lancement au
  démarrage de Windows), la croix fermait LiveDeck et coupait les decks. Elle réaffiche l'icône, ou réduit dans la
  barre des tâches si la zone de notification est absente. Fermer la session Windows n'est plus bloqué par LiveDeck.

## 1.9.0 — 2026-11-11

### Éditeur de touche plus direct
- **L'action d'abord** : après avoir choisi « Take », « Program »… le choix de l'input est tout de suite sous les yeux,
  au lieu d'être sous l'apparence et trois rangées d'options. L'ordre est maintenant Actions, Apparence, Retour d'état.
- **⚙ Options** : Interrupteur, appui long et Sécurité sont repliés tant que la touche ne s'en sert pas.
- **Une étape = une carte** : la liste des étapes n'apparaît qu'à partir de deux ; dupliquer et supprimer sont dans la
  carte, **＋ Étape** et **＋ Pause** juste en dessous.
- **Sections repliables** (clic sur le titre, mémorisé) ; le retour d'état automatique est replié par défaut.
- **Touche vide** : chaque proposition montre la vignette de la touche qui sera créée ; même chose dans l'onglet
  *Boutons* de la bibliothèque. La case sélectionnée affiche un « + ».
- Rien de sélectionné : l'éditeur explique les trois gestes utiles au lieu de rester vide.
- Les messages (erreurs, alertes, confirmations) s'affichent dans un bandeau, plus dans la ligne d'aide.
- Les flèches de défilement des onglets de pages sont plus grandes et lisibles.

### Corrections
- **Loupedeck CT** : toucher l'écran de la molette pendant qu'une vignette est maintenue ne relâche plus celle-ci
  (ni l'inverse) : les deux écrans numérotent leurs doigts séparément.
- **Noms d'input corrigés automatiquement** : les molettes et boutons physiques sont enfin corrigés eux aussi (la
  correction était annoncée et la configuration réenregistrée en boucle, sans effet).
- **Import Companion** : les boutons en trop sont répartis sur autant de pages « (suite) » qu'il en faut ; ils
  pouvaient se retrouver hors de la grille, invisibles.
- **Configuration éditée à la main** : une `version` nulle, un bloc `vmix` sans port ou une option nulle ne font plus
  classer le fichier comme abîmé, ni planter le démarrage.
- **Surveillance réseau** : une cible donnée par son nom (résolue en IPv6) n'est plus « hors ligne » en permanence ;
  le fil de surveillance ne s'arrête plus sur un réglage inattendu.
- **Tallys** : plus d'empilement de sondages quand des tallys sont injoignables (état affiché en retard).
- **Raccourcis** : « ctrl++ » (ou « ctrl+plus ») envoie bien la touche +.
- Une action mal formée n'empêche plus les suivantes de partir ; l'export écrit sans les clés internes et signale
  un fichier impossible à écrire ; charger un fichier qui n'est pas une configuration est refusé proprement.
- Mise à jour : le nom du fichier téléchargé ne peut plus sortir du dossier temporaire.

## 1.8.0 — 2026-11-09

### Licence
- Le dépôt est public, sous licence [PolyForm Noncommercial 1.0.0](LICENSE) : usage non commercial libre.

### Mise à jour dans l'app
- **LiveDeck cherche tout seul la dernière release GitHub** une dizaine de secondes après le démarrage. Quand une version
  plus récente existe, un bouton « Mise à jour x.y.z » apparaît au-dessus du deck, avec les notes de la version.
- **Installer et redémarrer** : l'installateur est téléchargé (taille et empreinte SHA-256 vérifiées), LiveDeck se ferme,
  la mise à jour s'installe en silencieux puis LiveDeck se relance. Ta configuration n'est jamais touchée.
- **Réglages → Général → Mises à jour** : « Rechercher maintenant », et une case pour désactiver la recherche au démarrage.
- La mise à jour n'est jamais automatique : rien ne se ferme sans ton clic, et la fenêtre te prévient si tu es en Live
  ou verrouillé.

## 1.7.0 — 2026-11-06

### orchestream-v2 : commandes régie
- **Les touches pilotent la régie d'orchestream-v2** par son API Stream Deck : jeu suivant, question
  suivante ou fermée, fin de partie, tirage et retirage, Twizz et Speedizz, fin du sondage, prédiction,
  remercier, rejouer la dernière popup, effacer les popups, vider les files, scène DMAIX, afficher ou
  masquer un widget, fonctions vMix autorisées, début et fin du live.
- **La touche montre ce qu'elle va faire** : « Fermer Q3 », « Ouvrir Q4 », « Terminer » ; le nombre de
  viewers à remercier ; le live en cours. Elle se **grise avec la raison d'orchestream** quand la commande
  est impossible (« Aucune partie en cours »), et affiche **NON AUTORISÉ** si le jeton n'en a pas la
  permission — avant même qu'on appuie.
- Jeton API à coller dans Réglages → Intégrations, avec un bouton **Tester**. Il part dans l'en-tête, jamais
  dans l'adresse. Le quiz d'un Twizz se choisit dans la liste fournie par orchestream.
- Les limites d'orchestream sont respectées : état relu toutes les 1,5 s, et délai demandé attendu en cas
  de trop de requêtes.
- Neuf boutons prêts dans la bibliothèque, catégorie *Intégrations*.

### Corrections
- Une molette dont seule la rotation utilisait une intégration ne la réveillait pas.

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
