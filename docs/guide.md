# 📘 Guide d'utilisation

← [Retour au README](../README.md)

## Sommaire

- [Premier lancement](#premier-lancement)
- [L'interface](#linterface)
- [Créer des touches vite](#créer-des-touches-vite)
- [Les étapes d'une touche](#les-étapes-dune-touche)
- [Take : preview puis antenne](#take--preview-puis-antenne)
- [Texte dynamique](#texte-dynamique)
- [Retours d'état automatiques](#retours-détat-automatiques)
- [Ce qui change par rapport à Companion](#ce-qui-change-par-rapport-à-companion)
- [Raccourcis](#raccourcis)
- [Intégrations maison](#intégrations-maison)
- [Commandes réseau : OSC, TCP / UDP, API JSON](#commandes-réseau--osc-tcp--udp-api-json)
- [Diagnostic Stream Deck](#diagnostic-stream-deck)
- [Mises à jour](#mises-à-jour)
- [Barre des tâches et démarrage automatique](#barre-des-tâches-et-démarrage-automatique)
- [Sauvegardes](#sauvegardes)
- [Dépannage](#dépannage)

## Premier lancement

1. Ferme Bitfocus Companion et le logiciel Elgato Stream Deck : une seule application à la fois peut piloter un Stream Deck.
2. Lance LiveDeck, puis **Fichier → Config Bitfocus Companion…** pour reprendre ta configuration existante.
   Deux options te sont proposées avant l'import :
   - **2e appui sur une touche Preview** : laisse « Rien, comme dans Companion », ou choisis une transition pour
     qu'un 2e appui sur un input déjà en preview l'envoie à l'antenne ;
   - **Convertir vers les intégrations natives** : à cocher seulement si tu utilises py-intercom, orchestream ou
     Tally32 (voir [Intégrations maison](#intégrations-maison)). Avec **Ajouter les pages**, les réglages trouvés
     (adresse orchestream, tallys) complètent les tiens sans les remplacer.

   Chaque Stream Deck retrouve sa page grâce à son numéro de série.
3. Côté vMix, rien à configurer : LiveDeck utilise l'API TCP (port 8099), active par défaut.
   Si vMix tourne sur un autre PC, indique son adresse dans **Réglages**.

## L'interface

![LiveDeck](images/surface.png)

| Zone | Rôle |
|---|---|
| **En-tête** | Pastilles d'état : Stream Decks (clic = Diagnostic), vMix (version, latence ; clic = Réglages) et une pastille par intégration utilisée (clic = Réglages → Intégrations). Puis Édition / Live, *Suivre le deck*, **Fichier**, Moniteur, Réglages |
| **Gauche** | **Inputs vMix** en direct (pastille rouge = antenne, verte = preview, badge OV = en overlay) et **Boutons** prêts à l'emploi, avec recherche |
| **Centre** | Le Stream Deck à l'écran, avec sa colonne d'état (deck, vMix, REC, STREAM, PGM, PVW). Un onglet par page ; ● = page affichée sur un deck branché |
| **Droite** | L'inspecteur de la touche : apparence, étapes, retour d'état, **▶ Tester** |

- Les colonnes de gauche et de droite se **redimensionnent** en tirant leur bord ; LiveDeck retient leur largeur.
  Sur un petit écran, l'inspecteur passe les libellés au-dessus des champs au lieu de défiler sur le côté.
- **Suivre le deck** : appuyer sur une touche physique la sélectionne dans l'éditeur (et change de page si besoin).
  Chaque appui fait flasher la touche à l'écran.
- **Survoler une touche** du deck à l'écran affiche ce qu'elle fait, son prochain appui et ses problèmes éventuels
  (input introuvable, py-intercom indisponible).
- **Édition / Live (F5)** : en Live, un clic déclenche la touche comme sur le deck.
- **Verrouiller (Ctrl+L)** : pour un direct, bloque l'édition (bibliothèque, éditeur, pages, import, réglages) et les
  clics sur les touches de l'écran : plus de fausse manip à la souris. Les Stream Decks physiques continuent de
  fonctionner. Le verrou est immédiat, déverrouiller demande une confirmation, et il est conservé si LiveDeck est
  relancé. Disponible aussi dans le menu de l'icône près de l'horloge.
- **Moniteur** : journal horodaté des actions envoyées, des alertes et des erreurs (commande refusée par vMix,
  HTTP en échec, py-intercom injoignable…). Le bouton affiche le nombre d'erreurs et d'alertes non lues ; un filtre
  montre *Erreurs et alertes* ou *Actions envoyées*, avec **Copier**, **Effacer** et **Fichier journal**.
  Les erreurs et alertes s'affichent aussi en bandeau en bas de la fenêtre : clique dessus pour ouvrir le Moniteur.
- **Fichier** : importer (Companion, configuration LiveDeck, une page), exporter (la configuration, la page
  affichée), Diagnostic Stream Deck, raccourcis et légende (**F1**), dossier des données.
- **Rechercher (Ctrl+F)** : retrouve une touche sur toutes les pages par son texte, son input, son action ou sa page.

![Recherche](images/recherche.png)

En mode **Live**, l'édition est verrouillée (inspecteur, collage, suppression, dépôts) : la colonne de droite montre
la touche en lecture seule (ce qu'elle fait, son prochain appui, les inputs manquants, l'état des intégrations) et
seul **▶ Tester** reste actif. De quoi laisser l'écran ouvert pendant le direct sans risque.

## Créer des touches vite

- **Touche vide** : l'inspecteur propose tout ce qu'une touche peut faire, rangé par famille (Vidéo, Production,
  Audio, Intégrations, Clavier, Web, Réseau, Deck…), avec un champ de recherche et des puces de catégorie.
  Une intégration pas encore réglée est marquée « à configurer » : la touche se crée quand même, et son
  inspecteur te mène aux **Réglages…**. Take, Program, Preview, Overlay et Son ouvrent directement la liste des
  inputs.
- **Une rangée en quelques secondes** : sélectionne la première touche, puis double-clique les inputs
  un par un dans la colonne de gauche. Chaque double-clic assigne l'input et passe à la touche suivante.
- **Remplir la rangée…** (clic droit sur une touche, ou sur la liste des inputs) : choisis un style (Take,
  Preview + Program, Preview, Program, Overlay 1-4, Couper le son) et les inputs à placer, la rangée est remplie à
  partir de la touche choisie. Une seule étape d'annulation.
- **Glisser-déposer** : glisse un ou plusieurs inputs sur le deck. Le menu « Créer » choisit le type
  (Take, Program, Preview, Preview + Program sur 2 rangées, Overlay 1-4, Couper le son).
- **Bibliothèque « Boutons »** : CUT, Fade, Stinger, FTB, REC, STREAM, MultiCorder, talkback py-intercom, touches
  orchestream, raccourci maintenu, HTTP, OSC, TCP, interrupteur, liens directs vers chaque page… à glisser sur une
  touche ou à double-cliquer. Les bus py-intercom et les touches qu'orchestream et py-multiview décrivent y
  apparaissent en direct.
- **Double-clic sur une touche existante** : un input double-cliqué remplace son input, et son texte suit.
- **Convertir en Take** : clic droit sur une touche Preview ou Program, sur sa rangée, ou menu de l'onglet pour
  toute la page.
- **Partager une page** : clic droit sur l'onglet → **Exporter la page…** / **Importer une page…** (fichier `.json`).
  Une page importée s'ajoute aux tiennes sans rien écraser.

![Touche vide](images/touche-vide.png)

## Les étapes d'une touche

L'éditeur montre d'abord l'**action** de la touche (c'est là que tu choisis l'input), puis son *Apparence*
et son *Retour d'état*. Un clic sur le titre d'une section la replie ou la déplie.

Une touche enchaîne une ou plusieurs étapes (ex. *Preview · Waiting Screen* puis *Stinger1*). Avec une seule
étape, sa carte s'affiche directement ; à partir de deux, une liste apparaît : sélectionne une étape pour la
modifier, glisse-la pour la réordonner. **＋ Étape** en ajoute une, **＋ Pause** un délai ; ⧉ et ✕ dans la carte
dupliquent ou suppriment l'étape. Changer le type d'une étape garde ses réglages compatibles (input, mix…).

**⚙ Options**, à droite du titre *Actions*, ouvre les réglages avancés : mode **Interrupteur**, **appui long**
et **Sécurité**. Ils s'affichent d'eux-mêmes sur une touche qui s'en sert déjà.

Sous le titre de la touche, **Prochain appui** dit en clair ce que fera le prochain appui
(« mettre en preview », « À L'ANTENNE (Fade) », « rien : l'input est déjà à l'antenne »…), en suivant l'état de vMix en direct.

En mode **Interrupteur**, deux listes : *À l'activation* et *À la désactivation*. L'état est mémorisé
et affiché sur la touche.

### Appui court / appui long

En mode Appui, une touche peut avoir une deuxième série d'actions sur **appui long** (½ s), par exemple
« preview » en appui court et « direct à l'antenne » en appui long. Une jauge se remplit en haut de la
touche pendant qu'on la maintient. Dès qu'un appui long est défini, l'appui court part au relâchement.

La case **Sécurité** impose de maintenir la touche ½ s pour la déclencher : idéal pour FTB ou l'arrêt du
stream (la bibliothèque propose *FTB sécurisé* et *Arrêt du stream*). Elle vaut aussi pour un **Interrupteur** :
un appui bref ne le fait pas basculer.

### Actions maintenues (talkback, raccourci maintenu)

Un talkback py-intercom en mode *Maintenu* ou un raccourci clavier *Maintenir* reste actif tant que tu gardes le
doigt sur la touche, quel que soit le type de touche :

| Touche | Le maintien commence… | … et s'arrête |
|---|---|---|
| Normale ou avec appui long | à l'appui | au relâché |
| Avec **Sécurité** | une fois la ½ s atteinte | au relâché |
| **Interrupteur** | au 1er appui (après la ½ s s'il est sécurisé) | au 2e appui |

Le relâché part toujours vers ce qui a été ouvert à l'appui, même si tu changes de page, modifies ou effaces la
touche pendant ce temps. Un interrupteur verrouillé est relâché dès que tu effaces la touche, la passes en
*Appui*, changes ses actions maintenues ou annules. Débrancher un Stream Deck relâche ce que ce deck tenait
(les autres decks gardent leur talkback) ; quitter LiveDeck relâche tout (raccourcis, interrupteurs, micros
py-intercom).

![Étapes](images/etapes.png)

## Take : preview puis antenne

L'action **Take** remplace la paire Preview + Program : une seule touche par input.

| Tu appuies quand la touche est… | Ce qui se passe |
|---|---|
| **sans couleur** (input ni en preview ni à l'antenne) | l'input passe en **preview** : la touche devient **verte** |
| **verte** (en preview, prêt à prendre) | la transition choisie (Cut, Fade, Stinger…) l'envoie **à l'antenne** : la touche devient **rouge** |
| **rouge** (déjà à l'antenne) | rien, pour ne pas couper l'image par erreur |

Un **liseré vert sous une touche rouge** veut dire que l'input est à l'antenne et aussi en preview. Le rouge
l'emporte toujours sur le vert. Deux appuis rapprochés marchent : LiveDeck tient compte de sa propre commande sans
attendre que vMix réponde. Le Mix (1 à 16) se choisit dans l'étape.

**Une touche Preview peut faire pareil** : dans l'étape *Preview*, l'option **2e appui (déjà en preview)** envoie
l'input à l'antenne avec la transition choisie si tu appuies alors qu'il est déjà en preview. Par défaut elle
reste sur « Rien », comme dans Companion. Pour passer d'un coup à Take, utilise **Convertir en Take**.

La liste des transitions reprend celle de vMix : effets (et leurs versions inversées), **Stinger 1-4** et
**Bouton transition 1-4** (la transition réglée sur ce bouton dans vMix). La durée à 0 garde celle de vMix.

## Texte dynamique

Le texte d'une touche peut contenir des **variables** remplacées en direct. Le menu **Variables…** à côté de
*Apparence* les insère pour toi. Exemple : `CLIP\n$(vmix:remaining:Magneto)` affiche le temps restant d'une vidéo.

| Variable | Affiche |
|---|---|
| `$(vmix:program_title)` / `$(vmix:preview_title)` | Titre de l'input à l'antenne / en preview (`:2` pour le Mix 2) |
| `$(vmix:program_number)` / `$(vmix:preview_number)` | Son numéro |
| `$(vmix:input_title:<input>)` / `$(vmix:input_number:<input>)` | Titre / numéro d'un input |
| `$(vmix:input_state:<input>)` | État de lecture (Running, Paused…) |
| `$(vmix:remaining:<input>)` | Temps restant d'une vidéo ou d'un son (m:ss) |
| `$(vmix:position:<input>)` / `$(vmix:duration:<input>)` | Position de lecture / durée totale |
| `$(vmix:volume:<input>)` | Volume (0-100) |
| `$(vmix:text:<input>:<champ>)` | Texte d'un titre : `Headline.Text`, `Headline` ou l'index `0` |
| `$(vmix:recording_duration)` / `$(vmix:streaming_duration)` | Durée de l'enregistrement / du stream |
| `$(time)` · `$(time:hm)` · `$(date)` | Heure (hh:mm:ss, hh:mm) et date |

`<input>` se désigne comme partout : numéro, titre ou GUID. Une valeur indisponible (vMix hors ligne, input
introuvable) affiche « — ». Seules les touches dont le texte change sont redessinées. À l'import Companion, les
variables courantes (`$(vmix:input_X_name)`, `$(vmix:input_X_remaining)`, `$(internal:time_hms)`…) sont converties.

## Retours d'état automatiques

Rien à configurer : la touche s'allume selon ce qu'elle fait.

| Touche | Retour |
|---|---|
| Take / Preview | fond vert en preview, **rouge à l'antenne** (liseré vert si aussi en preview) |
| Program | fond rouge à l'antenne, liseré vert en preview |
| Preview + transition (ex. Waiting Screen + Stinger) | traitée comme une touche Program |
| Overlay / PIP | fond jaune quand l'input est sur l'overlay, liseré vert en preview |
| Couper un son | fond rouge quand l'input est coupé, liseré vert quand il est actif |
| Rec / Stream / FTB / MultiCorder | rouge quand actif |
| Talkback py-intercom | bleu micro ouvert ; grisée avec HORS LIGNE, DÉSARMÉ ou BUS ABSENT ; bleu foncé PERMANENT pour un bus « Toujours actif » |
| Raccourci maintenu | bleu tant que la touche est enfoncée |
| Interrupteur | jaune quand il est actif |

**＋ Personnaliser** ajoute ses propres conditions : n'importe quel input, Mix 2 à 16, overlay, son,
état vMix, interrupteur, touche enfoncée, équipements réseau (ping ou HTTP), valeur d'une API JSON.

Un **point orange** sur une touche signale un input introuvable dans vMix. À la connexion, les noms
approchés importés de Companion (`cameraregie`) sont remplacés par le titre exact (« Camera Regie »).
La légende complète des couleurs est dans **F1**.

## Ce qui change par rapport à Companion

| Dans Companion | Dans LiveDeck |
|---|---|
| Action + feedback + couleurs à régler séparément | Le retour d'état est déduit de l'action |
| Variables `$(vmix:input_xxx_number)` | L'input se choisit par son nom dans la liste en direct |
| 2 touches Preview + Program par input | Une touche **Take** |
| VICREO Listener + press/release Shift puis A | Un raccourci `shift+a` avec **Maintenir**, enregistré en appuyant sur les touches |
| Variable custom + `logic_if` + 2 « set value » | Mode **Interrupteur** |
| generic-http rempli champ par champ | **Coller une commande curl…** |
| Modules OSC / TCP / UDP génériques | Actions **Message OSC** et **Commande TCP / UDP** |
| 6 pings + 3 GET + 5 variables d'expression | Retour **Équipements réseau** : vert si tout répond, rouge avec la liste des absents |

## Raccourcis

| Touche | Effet |
|---|---|
| Flèches | Déplacer la sélection |
| Entrée | Tester la touche sélectionnée |
| F2 | Modifier le texte de la touche |
| Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+D | Copier / couper / coller / dupliquer la touche |
| Suppr | Effacer la touche |
| Ctrl+Z / Ctrl+Y | Annuler / rétablir (toutes les modifications, réglages compris) |
| Ctrl+F | Rechercher une touche |
| Ctrl+Pg↑ / Ctrl+Pg↓ | Page précédente / suivante |
| F5 | Basculer Édition / Live |
| F1 | Raccourcis et légende des couleurs |
| Échap | Désélectionner |

Clic droit sur une touche : tester, copier, **convertir en Take**, **remplir la rangée**, **envoyer vers une autre
page**, effacer. Clic droit sur un onglet : renommer, taille de grille, dupliquer, convertir en Take, exporter /
importer la page, supprimer la page.

Dans **Réglages**, chaque Stream Deck (identifié par son numéro de série) est associé à sa page ;
le bouton **Identifier** l'allume en couleur pour savoir lequel est lequel. Modèles reconnus : Mini, Neo,
Original / MK.2, +, + XL, XL, Studio et Pedal (les touches tactiles du Neo changent de page).

La configuration est enregistrée automatiquement dans `%APPDATA%\LiveDeck\config.json`, indépendamment
de l'installation : mettre à jour ou désinstaller LiveDeck ne la touche pas
(**Fichier → Exporter → La configuration…** pour une copie).

## Intégrations maison

Réglages → **Intégrations**, et les boutons prêts dans la bibliothèque (catégorie *Intégrations*).
Chaque intégration ne travaille que si une touche l'utilise ; pendant que la fenêtre Réglages est ouverte,
elles sont toutes surveillées.

![Intégrations](images/integrations.png)

| Outil | Ce que fait LiveDeck | Retour sur la touche |
|---|---|---|
| **py-intercom** (API Stream Deck du client, `http://127.0.0.1:5080`, jeton facultatif) | Talkback maintenu (`on` à l'appui, `off` au relâché), basculer, ouvrir, fermer, couper tous les bus ; ou les touches que py-intercom décrit (une par bus, *Tout couper*, état de la liaison) | Bleu micro ouvert ; HORS LIGNE, DÉSARMÉ, BUS ABSENT ou PERMANENT sinon ; pour les touches décrites : texte, couleur et état donnés par py-intercom, en direct |
| **orchestream-v2** (API Stream Deck, jeton) | Toute touche qu'orchestream décrit : jeu suivant, tirage, Twizz, widget, raccourci Studio, remercier, fin du live, état d'une intégration… | Texte, couleur et état donnés par orchestream, en direct (« Fermer Q3 », « 3 à remercier », « Allumé ») ; grisée avec la raison quand elle ne peut pas servir |
| **Tally32** (`GET /status`, `POST /vmix/reconnect`) | Reconnecter un tally ou tous à vMix, surveillance toutes les 3 s | Vert si tous sont connectés à vMix, rouge avec le nom des tallys en défaut |
| **py-multiview** (multiviewer SRT/OMT, API Stream Deck, jeton facultatif) | Toute touche que py-multiview décrit : réception, un flux, le son d'un flux, émission OMT, retour audio et son volume, fenêtre de retour, route multicast | Texte, couleur et état donnés par py-multiview, en direct ; grisée avec la raison (« Aucun écran assigné. ») |
| **py-multiview**, anciennes touches (`GET /status`) | Démarrer / arrêter un flux, toute la réception, l'émission OMT, le retour audio ou la fenêtre de retour ; couper le son d'un flux ; régler le volume du retour | Vert quand c'est en marche, brun en défaut (attente, relance), liseré gris et HORS LIGNE si py-multiview ne répond pas |

### Régler py-intercom

1. Il faut un **client py-intercom qui sert l'API Stream Deck commune** (`/api/deck`) : les anciennes routes
   `/ptt/…` et `/status` n'existent plus.
2. Dans le client, **Réglages → Contrôle à distance** : coche **« Activer le contrôle HTTP »** (port 5080 par
   défaut). Si LiveDeck tourne sur un autre PC que le client, coche aussi **« Accepter les commandes du réseau
   local »** et indique l'IP du PC intercom dans LiveDeck. Un **jeton** est facultatif : vide, l'API est ouverte ;
   rempli, colle-le dans le champ *Jeton API* de LiveDeck.
3. Dans LiveDeck, Réglages → Intégrations : adresse, port et jeton du client, puis **Tester**. Le résultat dit en
   clair ce qui ne va pas : client fermé ou contrôle HTTP désactivé, mauvaise IP ou pare-feu, jeton, proxy, client
   désarmé (pas connecté au serveur intercom), bus absent…

py-intercom décrit aussi ses propres touches (**Touche d'une app**, app *py-intercom*, ou bibliothèque →
Intégrations) : une par bus, *Tout couper* et l'état de la liaison, mises à jour en direct.

La liste des **bus** vient du client lui-même : choisis-le dans l'étape *Intercom*. Sur la touche :

| Libellé | Signification |
|---|---|
| HORS LIGNE | le client ne répond pas (fermé, contrôle HTTP désactivé, réseau) |
| DÉSARMÉ | le client répond mais n'est pas connecté au serveur intercom |
| BUS ABSENT | ce bus n'est pas proposé par le client |
| PERMANENT | le bus est en « Toujours actif » dans le client : LiveDeck ne le coupe jamais au relâché |

L'inspecteur d'une touche Intercom affiche le même statut, avec **Tester la connexion** et **Réglages…**.

**Raccourci de secours** : dans l'étape *Intercom*, tu peux indiquer un raccourci clavier (par ex. celui de ton
ancien talkback). Si py-intercom ne répond pas, LiveDeck le simule à la place : maintenu tant que tu tiens la
touche en mode *Maintenu*, tapé en mode *Basculer*. Il n'est jamais utilisé quand le client est simplement désarmé
ou que le bus manque. Si le client était seulement lent, il a pu ouvrir le micro en retard : au relâché, LiveDeck
lui envoie donc aussi « off ». À la conversion d'une config Companion, l'ancien raccourci est gardé ici.

Les adresses locales (127.0.0.1, réseau privé, noms en `.local`) ne passent jamais par le proxy de Windows :
un proxy d'entreprise ne peut plus bloquer le talkback.

### Touches décrites par l'app (orchestream, py-multiview…)

orchestream-v2 et py-multiview parlent le même langage, l'**API Stream Deck** (`/api/deck`) : l'app décrit
elle-même chaque touche qu'elle propose et l'envoie en direct. Dans l'inspecteur, l'action **Touche d'une app**
choisit l'app puis la touche ; la touche enregistre l'app et l'identifiant de la touche
(`orchestream` + `game.advance`, `multiview` + `sender.toggle`). Le retour d'état **Touche d'une app** colore
une autre touche selon une touche d'une app. Ce qui suit vaut pour toutes ; seuls le jeton et l'adresse changent.

### orchestream-v2

orchestream décrit lui-même **chaque touche** que ton jeton peut poser : son texte, sa couleur, si elle est
allumée, si elle peut servir maintenant (et sinon pourquoi), et la commande qu'elle lance. LiveDeck n'a aucune
liste de commandes en dur : une nouveauté d'orchestream (raccourci Studio, sondage préparé, message du bot,
élément d'overlay…) apparaît sans mettre LiveDeck à jour.

1. Dans orchestream : **Réglages › Sécurité › Jetons API (Stream Deck)**, crée un jeton. Il ne s'affiche
   **qu'une fois** : copie-le. Ses **permissions** décident des touches proposées (« Piloter le live » pour
   terminer le live, par exemple).
2. Dans LiveDeck, Réglages → Intégrations, section orchestream : l'adresse **locale** du serveur (par ex.
   `http://192.168.1.20:5174`), le **Jeton API**, puis **Tester**. Le résultat donne le nom du jeton et le
   nombre de touches qu'il permet. Son adresse publique (tunnel, passerelle invités) est refusée : **Tester** te
   le dit et te propose l'adresse locale.
3. Pose une touche : bibliothèque, catégorie *Intégrations* (toutes les touches d'orchestream y sont, avec leur
   rubrique), ou l'action **Touche d'une app** (app *Orchestream*) dans l'inspecteur. La touche se choisit dans une liste groupée par
   rubrique (« Jeux », « Overlay », « Studio », « État »…), avec une recherche. Seul son identifiant est
   enregistré (`game.advance`, `overlay.toggle:twizz`, `studio.shortcut:…`).

Sur la touche :

| Affichage | Signification |
|---|---|
| titre + ce qui est en cours | tel qu'orchestream le décrit : « Fermer Q3 / Culture G », « Tout remercier / 3 à remercier », « Lumière plateau / Allumé » |
| couleur vive | la touche est **allumée** (widget affiché, lumière allumée, sondage en cours…) ; couleur sombre sinon |
| gris + raison | elle ne peut pas servir maintenant (« Aucune partie en cours. ») ; l'appui part quand même, orchestream revérifie |
| INTROUVABLE | cette touche n'existe plus dans orchestream (raccourci supprimé) ou n'est plus permise au jeton |
| HORS LIGNE, JETON REFUSÉ, JETON ?, ADRESSE ? | l'app ne répond pas, refuse le jeton réglé (révoqué), demande un jeton qui n'est pas réglé, ou rien n'est réglé |

Une touche de la rubrique « État » (live, Hype Train, intégrations d'orchestream) ne fait qu'afficher. Le retour
d'état **Touche d'une app** colore une autre touche selon une touche d'orchestream (allumée, éteinte,
indisponible) : par exemple une touche vMix qui s'allume quand le live est en cours.

Les touches arrivent par le **flux en direct** d'orchestream (`/api/deck/events`) : elles changent dans la
seconde, sans interroger le serveur. Flux coupé : LiveDeck se reconnecte tout seul, de plus en plus
espacé ; s'il ne peut pas ouvrir le flux alors qu'orchestream répond, il relit l'état chaque seconde en
attendant. Le jeton part dans l'en-tête `Authorization`, jamais dans l'adresse ; si orchestream signale trop de
requêtes, LiveDeck attend le délai demandé.

### Tally32

Un tally par ligne, `Nom = IP`. Si un tally répond sans connaître `/vmix/reconnect`, le Moniteur te demande de
mettre à jour son firmware.

### Régler py-multiview

Indique l'adresse du poste py-multiview : `192.168.1.42` suffit, le port `5090` est ajouté tout seul. Laissée
vide, c'est ce poste (`http://127.0.0.1:5090`).

Côté py-multiview, il faut ouvrir l'API : **Préférences → Pilotage à distance → Ouvrir l'API locale**, en
décochant *Ce poste uniquement* pour y accéder depuis cette machine. Le **Jeton API** est facultatif : ne le
remplis que si un jeton est réglé dans py-multiview (il part alors dans l'en-tête `Authorization`). **Tester**
vérifie l'API d'origine puis les touches décrites.

**Touches décrites** (py-multiview avec `/api/deck`) : bibliothèque, catégorie *Intégrations*, ou l'action
**Touche d'une app** (app *py-multiview*). Une touche par bascule : la réception, chaque flux, le son de chaque
flux, l'émission OMT, le retour audio, volume + et −, la fenêtre de retour, chaque route. Texte, couleur et état
viennent de py-multiview, en direct, comme pour orchestream ; un flux est désigné par son identifiant interne,
la touche reste donc juste si on le renomme ou le déplace.

**Anciennes touches** *py-multiview* (déjà posées) : elles continuent de marcher par l'API d'origine et ne sont
pas converties (elles ont des modes *Démarrer* / *Arrêter* et désignent un flux par son nom). Pour une nouvelle
touche, préfère les touches décrites.

Une ancienne touche choisit sa **cible** — un flux, toute la réception, l'émission OMT, le retour audio, la fenêtre de
retour, le son d'un flux ou une route multicast — et son **mode** : *Démarrer*, *Arrêter* ou *Basculer*.
« Basculer » est résolu à partir de l'état lu, donc deux appuis rapides ne se croisent pas.

Un flux se désigne par son **nom** ou par son **numéro** (`1`, `2`…) : la liste est proposée dès que LiveDeck
a vu py-multiview au moins une fois, et reste saisissable à la main sinon. Nommer le flux plutôt que le
numéroter garde la touche juste même si l'ordre change.

### Conversion depuis Companion

À l'import Companion, coche **Convertir vers les intégrations natives** pour convertir :
les touches *Intercom* (raccourcis Shift+A/Z/E via VICREO) vers l'API py-intercom (le raccourci est gardé en
secours), les requêtes show/hide vers les overlays d'orchestream vers ses touches `overlay.toggle:<widget>`
(jeton API à renseigner ensuite), et le bouton *Check Tally*
(pings + GET + variables) vers la surveillance native des Tally32. Une configuration LiveDeck 1.x est convertie
à l'ouverture ; les touches converties sont listées une fois dans le Moniteur.

## Commandes réseau : OSC, TCP / UDP, API JSON

Catégorie **Réseau** du menu ＋ Action, pour piloter d'autres logiciels et appareils sans module dédié.

| Action | Réglages | Exemple |
|---|---|---|
| **Message OSC** (UDP) | machine, port, adresse, arguments | QLab `127.0.0.1:53000`, adresse `/go` ; X32 `/ch/01/mix/on` avec `0` |
| **Commande TCP / UDP** | protocole, machine, port, données | projecteur `PWR ON\r` ; caméra VISCA en hexadécimal `81 01 04 00 02 FF` |

- **Arguments OSC**, séparés par des espaces : `1` (entier), `0.5` (réel), `true` / `false`, `"texte avec espaces"`.
  Un préfixe force le type : `i:` entier, `f:` réel, `s:` texte (`s:12` envoie le texte « 12 »).
- **Données TCP / UDP** : texte avec `\r`, `\n`, `\t`, `\0`, `\xNN`, ou coche **Données en hexadécimal**.
  LiveDeck envoie la trame sans attendre de réponse.

Le retour d'état **Valeur d'une API JSON** (＋ Personnaliser) lit une URL toutes les N secondes, va chercher une
valeur dans la réponse (`data.viewers`, `items[0].state`) et la compare (égal, différent, supérieur, inférieur,
contient, vrai, toujours, API en erreur). **Texte affiché** peut montrer la valeur lue : `Spectateurs\n{value}`.
Une valeur vrai / faux se compare avec `true`, `false`, `oui`, `non`, `1` ou `0`. L'URL est relue juste après un appui
sur la touche (réponse jusqu'à 4 Mo).

## Diagnostic Stream Deck

**Fichier → Diagnostic Stream Deck…**, ou clic sur la pastille des Stream Decks.

![Diagnostic](images/diagnostic.png)

| Onglet | Ce qu'il vérifie |
|---|---|
| **1 · Écrans** | Numérote chaque touche, affiche rouge / vert / bleu / blanc, fait varier la luminosité |
| **2 · Touches** | Appuie sur chaque touche : elle se coche à l'écran et s'allume en vert sur le deck |
| **3 · Performances** | Latence vMix, temps de lecture de l'état, temps d'affichage d'une touche, avec des conseils |

Pendant le diagnostic, les touches ne déclenchent rien (les actions maintenues sont relâchées avant).
À la fermeture, chaque deck retrouve la page qu'il affichait.

## Mises à jour

Quelques secondes après le démarrage, LiveDeck cherche la dernière [release GitHub](https://github.com/lfpoulain/py-livedeck/releases/latest).
S'il en existe une plus récente, un bouton **Mise à jour x.y.z** apparaît au-dessus du deck : il ouvre les notes de la version.
**Installer et redémarrer** télécharge l'installateur, vérifie sa taille et son empreinte SHA-256, ferme LiveDeck, installe
en silencieux et relance l'app. Rien ne démarre sans ton clic, et la fenêtre te prévient si tu es en Live ou verrouillé
(les Stream Decks s'arrêtent quelques secondes pendant l'installation).

**Réglages → Général → Mises à jour** : **Rechercher maintenant**, et une case pour ne plus chercher au démarrage.
Lancé depuis les sources, la recherche n'est pas automatique et LiveDeck ne s'installe pas lui-même : `git pull`.

## Barre des tâches et démarrage automatique

- Fermer la fenêtre la **réduit près de l'horloge** : les Stream Decks continuent de fonctionner. Clic
  droit sur l'icône pour afficher LiveDeck, passer en Live, lancer le diagnostic ou **Quitter**.
- **Réglages → Démarrage** : lancer LiveDeck à l'ouverture de session Windows (réduit près de l'horloge),
  démarrer directement en mode Live.
- Une seule instance à la fois : relancer LiveDeck ramène simplement la fenêtre au premier plan
  (deux instances se disputeraient les decks).

## Sauvegardes

- La configuration est écrite de façon sûre (fichier temporaire puis remplacement) : une coupure de courant ne
  la corrompt pas.
- Une copie est faite **chaque jour** et **avant chaque import** dans `%APPDATA%\LiveDeck\backups` (les 20 dernières
  sont gardées). **Fichier → Ouvrir le dossier des données** y mène directement.
- Si `config.json` est abîmé, LiveDeck le renomme `config.json.corrupt-<date>`, démarre avec une configuration
  vide et te le signale : recopie la dernière sauvegarde de `backups` à la place de `config.json` pour tout retrouver.
- Si `config.json` est seulement **verrouillé** (OneDrive, antivirus, éditeur ouvert), LiveDeck réessaie, puis
  démarre avec une configuration vide **sans jamais rien enregistrer**, pour ne pas écraser ton fichier : ferme ce
  qui le bloque et relance LiveDeck.

## Dépannage

| Symptôme | Piste |
|---|---|
| Aucun Stream Deck détecté | Companion ou le logiciel Elgato est encore ouvert : ferme-les, LiveDeck retrouve les decks tout seul (détection à chaud toutes les 2 s). |
| Point orange sur une touche | L'input n'existe pas (ou plus) dans vMix : choisis-le à nouveau dans l'inspecteur. |
| vMix « hors ligne » dans l'en-tête | Vérifie l'adresse dans **Réglages** et que l'API TCP de vMix (port 8099) n'est pas bloquée par le pare-feu. Un vMix figé est détecté après 3,5 s et LiveDeck se reconnecte seul. |
| Take : le 2e appui ne fait rien | La touche est rouge : l'input est déjà à l'antenne, Take ne le recoupe pas. Sur une touche **Preview**, le 2e appui ne fait rien par défaut : règle l'option *2e appui* ou convertis-la en Take. |
| **L'intercom ne marche pas** | Réglages → Intégrations → **Tester** : le message dit quoi faire. Le plus souvent : client py-intercom trop ancien (il faut celui qui sert `/api/deck`), « Activer le contrôle HTTP » non coché (Réglages → Contrôle à distance), « Accepter les commandes du réseau local » non coché si LiveDeck est sur un autre PC, ou jeton manquant. |
| Touche talkback HORS LIGNE | Le client ne répond pas : lance-le, active le contrôle HTTP, vérifie l'IP et le port (5080). Un raccourci de secours peut prendre le relais. |
| Touche talkback DÉSARMÉ | Le client tourne mais n'est pas connecté au serveur intercom : connecte-le dans py-intercom. |
| Touche talkback BUS ABSENT | Ce bus n'existe pas dans le client : choisis-en un autre dans l'étape *Intercom* (la liste vient du client). |
| Talkback refusé « proxy » | Un proxy intercepte la requête : les adresses locales le contournent déjà ; pour une IP publique, vérifie `HTTP_PROXY` ou le proxy de Windows. |
| Touche orchestream grise | La raison est écrite dessous (celle d'orchestream). HORS LIGNE ou JETON REFUSÉ : clique **Tester** dans Réglages → Intégrations. INTROUVABLE : la touche a disparu d'orchestream, choisis-en une autre dans l'inspecteur. |
| Une variable affiche « — » | vMix est hors ligne ou l'input de la variable est introuvable. |
| Une action ne fait rien | Ouvre le **Moniteur** : chaque refus de vMix (avec la commande refusée), d'un serveur HTTP, de py-intercom, d'orchestream ou d'un tally y est écrit avec son message. |

Le journal complet est dans `%APPDATA%\LiveDeck\livedeck.log` (**Moniteur → Fichier journal**).
