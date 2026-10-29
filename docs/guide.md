# 📘 Guide d'utilisation

← [Retour au README](../README.md)

## Sommaire

- [Premier lancement](#premier-lancement)
- [L'interface](#linterface)
- [Créer des touches vite](#créer-des-touches-vite)
- [Les étapes d'une touche](#les-étapes-dune-touche)
- [Retours d'état automatiques](#retours-détat-automatiques)
- [Ce qui change par rapport à Companion](#ce-qui-change-par-rapport-à-companion)
- [Raccourcis](#raccourcis)
- [Intégrations maison](#intégrations-maison)
- [Diagnostic Stream Deck](#diagnostic-stream-deck)
- [Barre des tâches et démarrage automatique](#barre-des-tâches-et-démarrage-automatique)
- [Dépannage](#dépannage)

## Premier lancement

1. Ferme Bitfocus Companion et le logiciel Elgato Stream Deck : une seule application à la fois peut piloter un Stream Deck.
2. Lance LiveDeck, puis **Importer → Config Bitfocus Companion…** pour reprendre ta configuration existante.
   Chaque Stream Deck retrouve sa page grâce à son numéro de série.
3. Côté vMix, rien à configurer : LiveDeck utilise l'API TCP (port 8099), active par défaut.
   Si vMix tourne sur un autre PC, indique son adresse dans **Réglages**.

## L'interface

| Zone | Rôle |
|---|---|
| **En-tête** | État des Stream Decks et de vMix (version, latence d'une commande), Édition / Live, *Suivre le Stream Deck*, Importer, Moniteur, Réglages |
| **Gauche** | **Inputs vMix** en direct (pastille rouge = antenne, verte = preview, badge OV = en overlay) et **Boutons** prêts à l'emploi |
| **Centre** | Le Stream Deck à l'écran, avec sa colonne d'état (deck, vMix, REC, STREAM, PGM, PVW). Un onglet par page ; ● = page affichée sur un deck branché |
| **Droite** | L'inspecteur de la touche : apparence, étapes, retour d'état, **▶ Tester** |

- **Suivre le Stream Deck** : appuyer sur une touche physique la sélectionne dans l'éditeur (et change de page si besoin). Chaque appui fait flasher la touche à l'écran.
- **Édition / Live (F5)** : en Live, un clic déclenche la touche comme sur le deck.
- **Moniteur** : journal horodaté des actions envoyées et des erreurs (commande refusée par vMix, HTTP en échec…).
- **Rechercher (Ctrl+F)** : retrouve une touche sur toutes les pages par son texte, son input, son action ou sa page.
- **Diagnostic** : vérifie les Stream Decks en une minute, comme dans MixPanel (voir plus bas).

En mode **Live**, l'édition est verrouillée (inspecteur, collage, suppression, dépôts) : seul **▶ Tester**
reste actif. De quoi laisser l'écran ouvert pendant le direct sans risque.

## Créer des touches vite

- **Une rangée en quelques secondes** : sélectionne la première touche, puis double-clique les inputs
  un par un dans la colonne de gauche. Chaque double-clic assigne l'input et passe à la touche suivante.
- **Glisser-déposer** : glisse un ou plusieurs inputs sur le deck. Le menu « Créer : » choisit le type
  (Take, Program, Preview, Preview + Program sur 2 rangées, Overlay 1-4, Couper le son).
- **Bibliothèque « Boutons »** : CUT, Fade, Stinger, FTB, REC, STREAM, MultiCorder, talkback, HTTP,
  interrupteur, liens directs vers chaque page… à glisser sur une touche ou à double-cliquer.
- **Double-clic sur une touche existante** : un input double-cliqué remplace son input, et son texte suit.
- **Touche vide** : l'inspecteur propose directement les types les plus courants.

![Touche vide](images/touche-vide.png)

## Les étapes d'une touche

Une touche enchaîne une ou plusieurs étapes (ex. *Preview · Waiting Screen* puis *Stinger1*). On
sélectionne une étape dans la liste pour la modifier ; glisser pour réordonner, **＋ Pause** pour
temporiser. Changer le type d'une étape garde ses réglages compatibles (input, mix…).

En mode **Interrupteur**, deux listes : *À l'activation* et *À la désactivation*. L'état est mémorisé
et affiché sur la touche.

### Take : une touche au lieu de deux

L'action **Take** remplace la paire Preview + Program : 1er appui, l'input passe en preview (touche
verte) ; 2e appui, la transition choisie (Cut, Fade, Stinger…) l'envoie à l'antenne (touche rouge).
Une rangée de Take remplace deux rangées de touches.

### Appui court / appui long

En mode Appui, une touche peut avoir une deuxième série d'actions sur **appui long** (½ s), par exemple
« preview » en appui court et « direct à l'antenne » en appui long. Une jauge se remplit en haut de la
touche pendant qu'on la maintient. Dès qu'un appui long est défini, l'appui court part au relâchement.

La case **Sécurité** impose de maintenir la touche ½ s pour la déclencher : idéal pour FTB ou l'arrêt du
stream (la bibliothèque propose *FTB sécurisé* et *Arrêt du stream*).

![Étapes](images/etapes.png)

## Retours d'état automatiques

Rien à configurer : la touche s'allume selon ce qu'elle fait.

| Touche | Retour |
|---|---|
| Program | fond rouge à l'antenne, liseré vert en preview |
| Preview | fond vert en preview, liseré rouge à l'antenne |
| Preview + transition (ex. Waiting Screen + Stinger) | traitée comme une touche Program |
| Overlay / PIP | fond jaune quand l'input est sur l'overlay, liseré vert en preview |
| Couper un son | fond rouge quand l'input est coupé, liseré vert quand il est actif |
| Rec / Stream / FTB / MultiCorder | rouge quand actif |
| Raccourci maintenu | bleu tant que la touche est enfoncée |
| Interrupteur | jaune quand il est actif |

**＋ Personnaliser** ajoute ses propres conditions : n'importe quel input, Mix 2 à 16, overlay, son,
état vMix, interrupteur, touche enfoncée, équipements réseau (ping ou HTTP).

Un **point orange** sur une touche signale un input introuvable dans vMix. À la connexion, les noms
approchés importés de Companion (`cameraregie`) sont remplacés par le titre exact (« Camera Regie »).

## Ce qui change par rapport à Companion

| Dans Companion | Dans LiveDeck |
|---|---|
| Action + feedback + couleurs à régler séparément | Le retour d'état est déduit de l'action |
| Variables `$(vmix:input_xxx_number)` | L'input se choisit par son nom dans la liste en direct |
| VICREO Listener + press/release Shift puis A | Un raccourci `shift+a` avec **Maintenir**, enregistré en appuyant sur les touches |
| Variable custom + `logic_if` + 2 « set value » | Mode **Interrupteur** |
| generic-http rempli champ par champ | **Coller une commande curl…** |
| 6 pings + 3 GET + 5 variables d'expression | Retour **Équipements réseau** : vert si tout répond, rouge avec la liste des absents |

## Raccourcis

| Touche | Effet |
|---|---|
| Flèches | Déplacer la sélection |
| Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+D | Copier / couper / coller / dupliquer la touche |
| Suppr | Effacer la touche |
| Ctrl+Z / Ctrl+Y | Annuler / rétablir (toutes les modifications) |
| Ctrl+F | Rechercher une touche |
| F5 | Basculer Édition / Live |
| Échap | Désélectionner |

Clic droit sur une touche : tester, copier, **envoyer vers une autre page**, effacer.
Clic droit sur un onglet : renommer, taille de grille, dupliquer, supprimer la page.

Dans **Réglages**, chaque Stream Deck (identifié par son numéro de série) est associé à sa page ;
le bouton **Identifier** l'allume en couleur pour savoir lequel est lequel.

La configuration est enregistrée automatiquement dans `%APPDATA%\LiveDeck\config.json`, indépendamment
de l'exe : on peut le remplacer par une nouvelle version sans rien perdre
(**Importer → Exporter la configuration…** pour une sauvegarde).

## Intégrations maison

Réglages → **Intégrations**, et les boutons prêts dans la bibliothèque (catégorie *Intégrations*).
Chaque intégration ne travaille que si une touche l'utilise.

| Outil | Ce que fait LiveDeck | Retour sur la touche |
|---|---|---|
| **py-intercom** (API du client, `http://127.0.0.1:5080`) | Talkback maintenu (`on` à l'appui, `off` au relâché), basculer, ouvrir, fermer, couper tous les bus | Bleu micro ouvert, gris quand le client est désarmé ou injoignable |
| **orchestream** (overlays Twizz, Sondage, Speedizz) | Afficher / masquer / basculer, avec l'état suivi en direct par le flux `/api/overlay/events` | Jaune quand l'overlay est affiché, liseré gris si l'état est inconnu |
| **Tally32** (`GET /status`, `POST /vmix/reconnect`) | Reconnecter un tally ou tous à vMix, surveillance toutes les 3 s | Vert si tous sont connectés à vMix, rouge avec le nom des tallys en défaut |

À l'import Companion (ou à l'ouverture d'une configuration 1.x), LiveDeck convertit automatiquement :
les touches *Intercom* (raccourcis Shift+A/Z/E via VICREO) vers l'API py-intercom, les interrupteurs
show/hide d'orchestream vers l'overlay suivi en direct, et le bouton *Check Tally* (pings + GET +
variables) vers la surveillance native des Tally32.

Limite connue : orchestream ne mémorise pas l'état de Twizz et du Sondage côté serveur. Au démarrage,
LiveDeck l'ignore (liseré gris) jusqu'au premier changement ; Speedizz, lui, est connu tout de suite.

## Diagnostic Stream Deck

| Onglet | Ce qu'il vérifie |
|---|---|
| **1 · Écrans** | Numérote chaque touche, affiche rouge / vert / bleu / blanc, fait varier la luminosité |
| **2 · Touches** | Appuie sur chaque touche : elle se coche à l'écran et s'allume en vert sur le deck |
| **3 · Performances** | Latence vMix, temps de lecture de l'état, temps d'affichage d'une touche, avec des conseils |

Pendant le diagnostic, les touches ne déclenchent rien.

## Barre des tâches et démarrage automatique

- Fermer la fenêtre la **réduit près de l'horloge** : les Stream Decks continuent de fonctionner. Clic
  droit sur l'icône pour afficher LiveDeck, passer en Live, lancer le diagnostic ou **Quitter**.
- **Réglages → Démarrage** : lancer LiveDeck à l'ouverture de session Windows (réduit près de l'horloge),
  démarrer directement en mode Live.
- Une seule instance à la fois : relancer LiveDeck ramène simplement la fenêtre au premier plan
  (deux instances se disputeraient les decks).

## Dépannage

| Symptôme | Piste |
|---|---|
| Aucun Stream Deck détecté | Companion ou le logiciel Elgato est encore ouvert : ferme-les, LiveDeck retrouve les decks tout seul (détection à chaud toutes les 2 s). |
| Point orange sur une touche | L'input n'existe pas (ou plus) dans vMix : choisis-le à nouveau dans l'inspecteur. |
| vMix « hors ligne » dans l'en-tête | Vérifie l'adresse dans **Réglages** et que l'API TCP de vMix (port 8099) n'est pas bloquée par le pare-feu. |
| Touche talkback grise | Le client py-intercom est désarmé ou son API (port 5080) n'est pas active. |
| Overlay orchestream avec liseré gris | État inconnu tant que l'overlay n'a pas changé depuis le démarrage (limite d'orchestream). |
| Une action ne fait rien | Ouvre le **Moniteur** : chaque refus de vMix, d'un serveur HTTP, de py-intercom, d'orchestream ou d'un tally y est écrit avec son message. |

Le journal complet est dans `%APPDATA%\LiveDeck\livedeck.log`.
