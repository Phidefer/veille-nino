#!/usr/bin/env python3
"""Serveur local ENSO / ATLAS v2 — API, rafraîchissement et historique.

Par défaut, écoute uniquement 127.0.0.1. Ne jamais publier directement sans
reverse proxy et authentification. Aucune clé API n'est exposée au navigateur.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
import json
import threading
import time

ROOT=Path(__file__).parent.resolve()
DATA=ROOT/'data'/'state.json'
FEEDS=ROOT/'data'/'feeds.json'
REFRESH_LOCK=threading.Lock()
SECTOR_LOCK=threading.Lock()
SECTOR_STATE={'running':False,'message':'Aucune collecte en cours.'}

def read_json(path):
 return json.loads(path.read_text(encoding='utf-8'))

def run_sector():
 from collect_sector import collect,write_json
 from history import save_history
 SECTOR_STATE.update({'running':True,'message':'Collecte des flux externes en cours.'})
 try:
  f=collect(read_json(FEEDS))
  write_json(FEEDS,f)
  n=save_history(f)
  ok=sum(d.get('status')=='ok' for d in f['datasets'].values())
  SECTOR_STATE['message']=f'{ok}/6 flux actualisés ; {n} observation(s) historisée(s). Les éventuels échecs sont détaillés dans le tableau.'
 except Exception as e:
  SECTOR_STATE['message']='Échec du traitement : '+str(e)[:180]
 finally:
  SECTOR_STATE['running']=False
  SECTOR_LOCK.release()

class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,directory=str(ROOT),**kwargs)
 def do_GET(self):
  parts=urlsplit(self.path);path=parts.path
  if path=='/api/state':return self.json_response(read_json(DATA))
  if path=='/api/feeds':return self.json_response(read_json(FEEDS))
  if path=='/api/feeds-status':return self.json_response(SECTOR_STATE.copy())
  if path=='/api/health':
   s=read_json(DATA);f=read_json(FEEDS)
   return self.json_response({'status':'ok','last_checked':s.get('last_checked'),'source_health':s.get('source_health'),'feeds_checked_at':f.get('checked_at'),'feeds':{k:v.get('status') for k,v in f.get('datasets',{}).items()}})
  if path=='/api/history':
   from history import query_history
   q=parse_qs(parts.query);source=q.get('source',['rain'])[0];subject=q.get('subject',[None])[0]
   try:return self.json_response({'source':source,'items':query_history(source,subject=subject,limit=150)})
   except ValueError:return self.json_response({'message':'Source invalide'},400)
  if path in ('/','/index.html','/favicon.ico'):return super().do_GET()
  return self.send_error(404,'Fichier non publié')
 def do_POST(self):
  # Prevent cross-site browser forms from triggering local collection.
  origin=self.headers.get('Origin')
  if origin and urlsplit(origin).netloc != self.headers.get('Host',''):
   return self.json_response({'message':'Origine refusée'},403)
  if self.path=='/api/refresh-feeds':
   if not SECTOR_LOCK.acquire(blocking=False):return self.json_response({'message':'Collecte déjà en cours','running':True},202)
   SECTOR_STATE.update({'running':True,'message':'Collecte lancée.'})
   threading.Thread(target=run_sector,daemon=True).start()
   return self.json_response({'message':'Collecte lancée','running':True},202)
  if self.path!='/api/refresh':return self.send_error(404,'Route inconnue')
  if not REFRESH_LOCK.acquire(blocking=False):return self.json_response({'message':'Mise à jour ENSO déjà en cours'},409)
  try:
   from refresh_sources import refresh
   state,msg=refresh(DATA)
   status=200 if state.get('source_health',{}).get('success') else 503
   return self.json_response({'message':msg,'state':state},status)
  except Exception as e:
   return self.json_response({'message':'Échec de collecte NOAA/FAO','error':str(e)[:150]},503)
  finally:REFRESH_LOCK.release()
 def json_response(self,obj,status=200):
  data=json.dumps(obj,ensure_ascii=False).encode('utf-8')
  self.send_response(status)
  self.send_header('Content-Type','application/json; charset=utf-8')
  self.send_header('Cache-Control','no-store')
  self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
  self.send_header('Content-Length',str(len(data)))
  self.end_headers();self.wfile.write(data)

def schedule(interval_hours,domain):
 # Only runs while the process is running. A true background service requires
 # an OS scheduler, container restart policy, or systemd unit.
 lock=REFRESH_LOCK if domain=='climate' else SECTOR_LOCK
 while True:
  time.sleep(interval_hours*3600)
  if lock.acquire(blocking=False):
   if domain=='sector':run_sector()
   else:
    try:
     from refresh_sources import refresh
     _,msg=refresh(DATA);print('Climat:',msg,flush=True)
    except Exception as e:print('Climat:',e,flush=True)
    finally:lock.release()

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser()
 p.add_argument('--host',default='127.0.0.1',help='127.0.0.1 recommandé ; ne pas exposer sans authentification')
 p.add_argument('--port',type=int,default=8080)
 p.add_argument('--auto-hours',type=float,default=12,help='Collecte NOAA/FAO, 0 pour désactiver')
 p.add_argument('--sector-hours',type=float,default=24,help='Collecte sectorielle, 0 pour désactiver')
 args=p.parse_args()
 if args.auto_hours>0:threading.Thread(target=schedule,args=(args.auto_hours,'climate'),daemon=True).start()
 if args.sector_hours>0:threading.Thread(target=schedule,args=(args.sector_hours,'sector'),daemon=True).start()
 server=ThreadingHTTPServer((args.host,args.port),Handler)
 if args.sector_hours>0 and not read_json(FEEDS).get('checked_at') and SECTOR_LOCK.acquire(blocking=False):
  threading.Thread(target=run_sector,daemon=True).start()
 print(f'ENSO / ATLAS v2 : http://{args.host}:{args.port}/index.html')
 print('Collecte sectorielle initiale lancée si aucune précédente. Le navigateur peut actualiser les vues.')
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:server.server_close()
