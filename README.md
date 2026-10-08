# Bodet Player

Application web en français pour sélectionner des zones et envoyer l’audio d’un PC, d’un microphone ou d’un fichier au serveur Linux. Docker Compose démarre un seul service web, sans proxy, en HTTP, derrière votre reverse proxy HTTPS.

## État de la connexion Bodet

**Le mode `bodet` est implémenté et activé dans la configuration fournie.** Il encode le son en MP3 et émet les paquets MEL multicast décrits par sigma-caster / be-a-sigma. La réception sur vos enceintes reste à valider. Le mode `simulation` reste disponible sans émission réseau. Les noms des trois zones sont des exemples à remplacer.

Voir [le protocole, sa configuration et ses limites](docs/bodet-protocol.md). Cette intégration indépendante reprend le format décrit par le projet open source ; elle nécessite une validation sur site.

- [Notice officielle Harmonys Stream](https://static.bodet-time.com/images/stories/Pdfs/EN/Manuals/Harmonys/608151-User-manual-Stream-Apps-FR-EN.pdf)
- [Capture audio du navigateur](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)

## Installation et port

Copier `.env.example` vers `.env` si ce fichier n’existe pas encore. Conserver votre mot de passe existant lors d’une mise à jour.

```env
PLAYER_HOST=127.0.0.1
PLAYER_PORT=8080
PLAYER_PASSWORD=votre-mot-de-passe
PLAYER_ORIGIN=
```

`PLAYER_PORT` choisit le port web (1024 à 65535). `PLAYER_HOST` indique l’IP utilisée dans le navigateur, sans protocole ni port ; utiliser l’IP LAN du serveur pour un accès depuis un autre PC. Ouvrir `http://<PLAYER_HOST>:<PLAYER_PORT>`.

Sur Linux, le serveur utilise le réseau hôte pour permettre le multicast Bodet :

```sh
docker compose up -d --build --remove-orphans
```

Sur Windows, utiliser Docker Desktop en mode conteneurs Linux ; le port est publié explicitement :

```powershell
docker compose -f compose.windows.yaml up -d --build --remove-orphans
```

`--remove-orphans` retire l’ancien conteneur proxy lors de la mise à jour. Les paramètres multicast restent dans le volume `player_data`. Aucun certificat Caddy n’est généré par le projet. Les anciens volumes de certificats ne sont pas supprimés automatiquement.

Pour un test local sur Windows, choisir `PLAYER_HOST=127.0.0.1` puis ouvrir `http://127.0.0.1:8080` (adapter le port). En HTTP sur une IP LAN, la connexion à l’interface fonctionne mais le navigateur ne permet pas la capture audio : cette capture nécessite HTTPS ou une adresse de boucle locale sur le PC qui héberge l’application.

## Votre reverse proxy

L’application ne contient aucun proxy ni serveur HTTPS. Configurer votre reverse proxy pour transmettre les requêtes vers `http://<IP_DU_SERVEUR>:<PLAYER_PORT>` et autoriser les connexions WebSocket (`/api/live`). Si votre proxy est un conteneur sur le même réseau Docker, utiliser `http://player:<PLAYER_PORT>` ; son propre `127.0.0.1` désigne le conteneur proxy.

Définir dans `.env` l’adresse publique exacte ouverte dans le navigateur, sans chemin ni barre oblique finale :

```env
PLAYER_HOST=192.168.1.50
PLAYER_PORT=8080
PLAYER_ORIGIN=https://192.168.1.50
```

Si votre proxy publie HTTPS sur 8443, utiliser `PLAYER_ORIGIN=https://192.168.1.50:8443`. `PLAYER_PORT` reste le port HTTP de l’application en amont du proxy. Le certificat est géré par votre proxy et doit être approuvé sur les PC clients pour la capture audio. `PLAYER_ORIGIN` configure le contrôle d’origine et les cookies de connexion ; utiliser cette adresse pour accéder à l’application. Laisser cette variable vide pour un test HTTP direct sur `http://PLAYER_HOST:PLAYER_PORT`.

Après modification de `.env`, relancer Compose. Restreindre l’accès au port HTTP au reverse proxy si nécessaire. Le mot de passe est commun aux opérateurs ; les sessions durent huit heures et sont invalidées au redémarrage. La capture sur une IP LAN nécessite l’accès HTTPS par votre proxy. Le test local `http://127.0.0.1:8080` fonctionne sur le PC hébergeant l’application. La configuration fournie active la diffusion Bodet : vérifier les zones et le multicast avant de démarrer. L’émission multicast depuis Docker Desktop vers le LAN reste à valider ; privilégier Linux en réseau hôte pour la diffusion.

## Utilisation

Cliquer sur le bouton bleu **Paramètres** en bas à droite. Dans **Configuration réseau**, saisir manuellement l’adresse multicast IPv4 de l’installation et cliquer sur **Enregistrer l’adresse**. La valeur initiale d’exemple est `239.192.55.1` : vérifier qu’elle correspond à Sigma. Seules les adresses de `224.0.0.0` à `239.255.255.255` sont acceptées. Arrêter toute diffusion avant de changer l’adresse. Le paramètre est conservé après redémarrage dans le volume Docker `player_data` ; `docker compose down -v` efface ce volume. Le mode simulation n’envoie toujours aucun paquet vers les enceintes.

Ouvrir l’interface sur le PC source, se connecter, sélectionner les zones puis une source et démarrer. Le partage du son système dépend du navigateur et du système : choisir une surface proposant l’audio et activer le partage audio. Un onglet audio peut être utilisé si la capture système n’est pas proposée. Le navigateur impose une autorisation à chaque partage ; un autre PC ne peut pas capturer le son à distance sans participation du PC source.

Pour un fichier audio, choisir un fichier local : il est décodé et transmis en direct sans stockage sur le serveur. Garder la page ouverte. Fermer la page coupe la session. Un opérateur connecté peut arrêter la diffusion depuis son interface. Une seule source peut diffuser à la fois ; une source silencieuse qui n’envoie plus de données est déconnectée après dix secondes. Le transport WebSocket peut avoir une latence supérieure à WebRTC, notamment sur un réseau congestionné.

Dans le même panneau, **Gérer les zones** permet d’ajouter, modifier ou supprimer une zone. Chaque zone possède un numéro unique entre 1 et 100 et un nom. Les zones sont conservées avec l’adresse multicast dans le volume Docker. Les réglages sont bloqués pendant une diffusion. Les changements mettent à jour la liste des zones sans recharger la page ; ils ne reconfigurent pas la centrale Sigma.

## Délai de diffusion

Le navigateur demande une faible latence audio. La passerelle limite l’analyse initiale de FFmpeg et transmet les blocs MP3 disponibles sans attendre de remplir 1000 octets (125 ms à 64 kbit/s). Le rythme de diffusion et le format MEL restent conservés. Le délai réel dépend aussi du navigateur, du réseau et des buffers des enceintes ; il doit être mesuré sur l’installation. Pour appliquer une mise à jour sur le serveur : `docker compose up -d --build --force-recreate`.

## Passerelle externe facultative

Le mode `bridge` lance le programme configuré dans `bridge_command` (tableau d’arguments, sans shell). Le programme doit être inclus dans une image personnalisée et accessible à l’utilisateur 10001. Il reçoit :

- sur l’entrée standard : PCM mono signé 16 bits little endian, 48 kHz, flux continu ;
- `BODET_ZONES` : tableau JSON des numéros de zones ;
- `BODET_MULTICAST_ADDRESS` : adresse multicast enregistrée depuis l’interface, figée pour la durée de la diffusion ;
- `AUDIO_FORMAT=s16le`, `AUDIO_RATE=48000`, `AUDIO_CHANNELS=1`.

Cette passerelle doit gérer le protocole Bodet réel, le début et la fin de diffusion, les priorités et les zones. Elle doit libérer la diffusion lorsqu’elle reçoit SIGTERM ou quand l’entrée se ferme. Ce contrat ne fournit pas cette implémentation. Aucun exécutable arbitraire ne peut être configuré depuis l’interface web. Les fichiers de configuration sont montés en lecture seule.

## Vérification et exploitation

```sh
docker compose config
docker compose logs -f player
docker compose down
```

Tests locaux : `python -m pytest tests`. Installer `pytest` et `httpx` en plus de `requirements.txt` dans l’environnement de développement. Les tests couvrent l’authentification, les zones, l’exclusion de deux diffusions et l’arrêt. La validation sur les enceintes et le protocole restent nécessaires avant tout usage réel.
