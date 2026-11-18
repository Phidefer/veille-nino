# ENSO / ATLAS — Image Docker autonome + Compose

Livraison : 18 novembre 2026 · image **Linux amd64** (x86-64) pour un NAS / serveur Docker compatible. Application : observatoire ENSO, version 2 avec flux OSINT. L'image est préconstruite : il n'est **pas nécessaire** d'accéder à Docker Hub pour l'installer.

## Installation par terminal sur le NAS (TOS / Docker)

Transférer `enso-atlas_2.1.0_linux-amd64_image.tar.gz` et `compose.yml` dans un même dossier sur le NAS, par exemple `/Volume1/docker/enso-atlas` (le chemin exact peut varier sur TOS).

Dans ce dossier :

```sh
docker load -i enso-atlas_2.1.0_linux-amd64_image.tar.gz
# Contrôler l'image :
docker image ls enso-atlas
# Lancer le service :
docker compose -f compose.yml up -d
# Vérifier les journaux et l'état :
docker compose -f compose.yml logs --tail=80
docker compose -f compose.yml ps
```

Par défaut, le port est lié à `127.0.0.1:8080` sur le NAS, pour **éviter de l'exposer à tous les appareils**. Depuis le NAS : `http://127.0.0.1:8080`. Pour consulter depuis un ordinateur sur le réseau local :

1. Copier `.env.example` en `.env` dans le même dossier que le Compose.
2. Remplacer `ENSO_BIND_IP=127.0.0.1` par l'**IP locale réelle** du NAS (par exemple `192.168.1.111`, uniquement si elle correspond encore au NAS).
3. Relancer `docker compose -f compose.yml up -d`.
4. Accéder à `http://ADRESSE_IP_DU_NAS:8080` depuis le réseau local.

**Sécurité :** le serveur intégré n'a pas d'authentification. Ne publiez pas le port 8080 sur Internet. Pour BunkerWeb, utiliser un reverse proxy **avec authentification et HTTPS**, et contrôler la connexion réseau entre les conteneurs avant toute ouverture WAN. BunkerWeb n'est ni modifié ni requis par ce Compose.

## Conservation des données

Le volume nommé `enso_atlas_data` conserve `state.json`, `feeds.json`, les caches de climatologie et `history.sqlite` même en cas de remplacement du conteneur. Ne **pas** exécuter `docker compose down -v` sauf si vous souhaitez effacer toutes ces données. Les collectes sont programmées à 12 h (NOAA / FAO) et 24 h (flux sectoriels), tant que le conteneur est en marche. Une collecte sectorielle initiale est déclenchée si aucun relevé antérieur n'est enregistré.

Sauvegarde simple de la base et des états :

```sh
# Selon la configuration du NAS, sauvegarder le volume Docker 'enso_atlas_data'
docker volume inspect enso_atlas_data
```

## Diagnostics et commandes utiles

```sh
docker compose -f compose.yml ps
docker compose -f compose.yml logs --tail=100
docker compose -f compose.yml restart
docker compose -f compose.yml down
```

Test HTTP depuis le NAS : `curl -fsS http://127.0.0.1:8080/api/health` (ou l'IP locale choisie). Les flux externes nécessitent une connexion Internet sortante (DNS + HTTPS). Certains fournisseurs peuvent limiter ou modifier leurs API : cela se verra dans la section **Flux & alertes**. Une absence de données ne signifie pas une absence de risque.

## Reconstruire ultérieurement l'image avec Docker

Le répertoire `sources/` et le `Dockerfile` sont fournis. Avec une machine ayant accès à Internet :

```sh
docker build -t enso-atlas:2.1.0-amd64 -f Dockerfile sources/
```

Le `Dockerfile` représente une **reconstruction fonctionnellement équivalente** sur une image Python officielle ; l'image livrée en `.tar.gz` a été assemblée hors ligne à partir de l'environnement Debian/Python disponible. Les versions exactes des dépendances et des bibliothèques système peuvent donc être différentes selon la date de reconstruction. Une veille des correctifs de sécurité et une reconstruction périodique restent nécessaires.

## Validation et limites

L'archive Docker a été produite et ses empreintes calculées. Le système de fichiers qu'elle contient a été extrait, puis testé dans un environnement `chroot` en utilisateur non privilégié : import de Python/requests/BeautifulSoup, lecture des données et démarrage du serveur, avec réponses HTTP 200 sur les API de lecture et refus des requêtes de mise à jour cross-origin. Six tests unitaires du projet sont passés.

**Limites importantes :** aucun moteur Docker ni accès aux registres d'images n'étaient disponibles au moment de la construction. L'import réel par `docker load`, l'exécution via Docker Compose et les collectes vers les API scientifiques sur Internet **n'ont pas été vérifiés ici**. L'archive est au format de sauvegarde Docker standard, mais il faut confirmer sa compatibilité sur le NAS.

L'image est **exclusivement `linux/amd64`**. Pour ARM64, il faut effectuer une construction dédiée avec le `Dockerfile` sur un hôte ARM64.
