#!/usr/bin/env python3
"""Collecte OSINT sectorielle pour ENSO / ATLAS.

Sources: ERA5 via Open-Meteo (points de veille), NASA EONET, FEWS NET,
OMS Disease Outbreak News et HCR. Aucune donnée chiffrée d'impact n'est
inférée à partir d'un phénomène ENSO. Les flux indépendants peuvent échouer.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlencode
import requests

ROOT = Path(__file__).resolve().parent
FILE = ROOT / 'data' / 'feeds.json'
BASE_DIR = ROOT / 'data' / 'baselines'
SOURCES = {
 'rain': {'name':'Copernicus ERA5 via Open-Meteo', 'url':'https://open-meteo.com/en/docs/historical-weather-api', 'frequency':'quotidien, retard d’environ 5–7 jours', 'method':'Points sentinelles, pas de moyennes régionales'},
 'fires': {'name':'NASA EONET', 'url':'https://eonet.gsfc.nasa.gov/docs/v3', 'frequency':'quotidien', 'method':'Événements répertoriés, pas surfaces brûlées'},
 'floods': {'name':'NASA EONET', 'url':'https://eonet.gsfc.nasa.gov/docs/v3', 'frequency':'quotidien', 'method':'Événements répertoriés, inventaire non exhaustif'},
 'prices': {'name':'FEWS NET Data Warehouse', 'url':'https://help.fews.net/fdw/fews-net-api', 'frequency':'mensuel ou variable', 'method':'Prix unitaires locaux, pays et unités non comparables directement'},
 'health': {'name':'OMS — Disease Outbreak News', 'url':'https://www.who.int/emergencies/disease-outbreak-news', 'frequency':'à chaque publication', 'method':'Bulletins sanitaires, pas incidence ni attribution ENSO'},
 'displacement': {'name':'HCR — Refugee Data Portal', 'url':'https://refugeedata.unhcr.org/en/documentation/technical-reference/api/', 'frequency':'variable, données parfois révisées', 'method':'Population de réfugiés hébergés, toutes causes confondues ; pas déplacés climatiques'},
 'harvests': {'name':'GEOGLAM / Crop Monitor', 'url':'https://www.cropmonitor.org/', 'frequency':'publication périodique', 'method':'Bulletins à valider manuellement ; aucune API intégrée'},
}
SENTINELS = [
 ('af-south','Harare, Zimbabwe',-17.83,31.05),('af-horn','Nairobi, Kenya',-1.29,36.82),
 ('af-lakes','Kampala, Ouganda',0.31,32.58),('as-indonesia','Kupang, Indonésie',-10.18,123.61),
 ('as-philippines','Manille, Philippines',14.60,120.98),('as-south','Dhaka, Bangladesh',23.81,90.41),
 ('am-central','Tegucigalpa, Honduras',14.07,-87.19),('am-amazon','Manaus, Brésil',-3.12,-60.02),
 ('am-peru','Piura, Pérou',-5.20,-80.63),('am-north','Seattle, États-Unis',47.61,-122.33),
 ('oc-pacific','Tarawa, Kiribati',1.33,172.98),('am-carib','Caracas, Venezuela',10.49,-66.88),
]
MARKET_COUNTRIES = [('ET','Éthiopie'), ('KE','Kenya'), ('MW','Malawi'), ('MZ','Mozambique'), ('ZM','Zambie'), ('GT','Guatemala')]
UNHCR_COUNTRIES = [('UGA','Ouganda'), ('ETH','Éthiopie'), ('KEN','Kenya'), ('ZMB','Zambie')]

class APIClient:
 def __init__(self):
  self.session=requests.Session();self.session.headers.update({'User-Agent':'ENSO-Atlas/1.0 (monitoring research; contact: local operator)', 'Accept':'application/json'})
 def get(self,url,params=None):
  r=self.session.get(url,params=params,timeout=(6,22),allow_redirects=True)
  r.raise_for_status()
  if len(r.content)>10_000_000:raise ValueError('Réponse trop volumineuse')
  return r.json()

# Adaptateurs purement déterministes, tests hors réseau.
def rows(data):
 if isinstance(data,list):return data
 if isinstance(data,dict):
  for name in ('results','Items','items','data','value'):
   if isinstance(data.get(name),list):return data[name]
 raise ValueError('Schéma de réponse non reconnu')

def grab(obj,*names):
 """Extract literal nested key or scalar. No interpretation of missing fields."""
 if not isinstance(obj,dict):return None
 for name in names:
  value=obj.get(name)
  if isinstance(value,dict):
   for key in ('name','label','value','code','title'):
    if value.get(key) not in (None,''):return value[key]
  elif value is not None and value!='':return value
 return None

def number(x):
 try:
  if x is None or isinstance(x,bool):return None
  n=float(x)
  return n if math.isfinite(n) else None
 except (TypeError,ValueError):return None

def short_date(x):
 if not x:return None
 x=str(x)[:10]
 try:return date.fromisoformat(x).isoformat()
 except ValueError:return None

def recent_rain(data):
 daily=data.get('daily',{})
 dates=daily.get('time',[]); vals=daily.get('precipitation_sum',[])
 if len(dates)!=len(vals) or len(vals)<25:raise ValueError('Série quotidienne insuffisante')
 usable=[number(v) for v in vals]
 pairs=[(d,v) for d,v in zip(dates,usable) if v is not None and v>=0 and short_date(d)]
 if len(pairs)<len(vals):raise ValueError('Jours de pluie manquants : fenêtre incomplète')
 return {'value':round(sum(v for _,v in pairs),1),'count':len(pairs),'from':pairs[0][0],'to':pairs[-1][0]}

def normal_rain(data):
 daily=data.get('daily',{})
 dates=daily.get('time',[]);vals=daily.get('precipitation_sum',[])
 if len(dates)!=len(vals):raise ValueError('Climatologie de longueur incohérente')
 sums=defaultdict(float);counts=defaultdict(int)
 for day,v in zip(dates,vals):
  n=number(v)
  if n is None or n<0 or not short_date(day):continue
  k=day[5:10];sums[k]+=n;counts[k]+=1
 norms={k:round(sums[k]/counts[k],5) for k in sums if counts[k]>=18}
 if len(norms)<355:raise ValueError('Climatologie incomplète ou trop courte')
 if '02-29' not in norms and '02-28' in norms and '03-01' in norms:
  norms['02-29']=round((norms['02-28']+norms['03-01'])/2,5)
 return norms

def rain_anomaly(recent,normal):
 days=[(date.fromisoformat(recent['from'])+timedelta(days=i)).strftime('%m-%d') for i in range((date.fromisoformat(recent['to'])-date.fromisoformat(recent['from'])).days+1)]
 if any(day not in normal for day in days):raise ValueError('Dates manquantes dans la normale ERA5')
 expected=round(sum(normal[day] for day in days),1)
 actual=recent['value'];pct=round(100*(actual/expected-1),1) if expected>=10 else None
 flag='indéterminé'
 if pct is not None:
  if pct<=-40 and expected>=25:flag='déficit marqué à examiner'
  elif pct>=50 and actual-expected>=25:flag='excédent marqué à examiner'
  else:flag='sans seuil franchi'
 return {'observed_mm':actual, 'normal_mm':expected,'anomaly_pct':pct,'flag':flag}

def get_normal(client,area,lat,lon,base_dir):
 base_dir.mkdir(parents=True,exist_ok=True)
 path=base_dir/(area+'.json')
 if path.exists():
  cached=json.loads(path.read_text(encoding='utf-8'))
  if cached.get('model')=='era5' and cached.get('period')=='1991-2020' and len(cached.get('mean_day',{}))>=355:return cached['mean_day']
 url='https://archive-api.open-meteo.com/v1/archive'
 params={'latitude':lat,'longitude':lon,'start_date':'1991-01-01','end_date':'2020-12-31','daily':'precipitation_sum','timezone':'UTC','models':'era5'}
 try:
  raw=client.get(url,params)
 except requests.HTTPError as err:
  # Some API gateways restrict maximum request span. Split into decades only
  # if the response explicitly indicates a client-side range problem.
  if err.response is None or err.response.status_code not in (400,413,422):raise
  times=[];values=[]
  for a,b in [(1991,2000),(2001,2010),(2011,2020)]:
   params.update({'start_date':f'{a}-01-01','end_date':f'{b}-12-31'})
   part=client.get(url,params)
   times.extend(part['daily']['time']);values.extend(part['daily']['precipitation_sum'])
  raw={'daily':{'time':times,'precipitation_sum':values}}
 normal=normal_rain(raw)
 # Atomic cache, baseline is reused across daily collections.
 write_json(path,{'model':'era5','period':'1991-2020','latitude':lat,'longitude':lon,'mean_day':normal})
 return normal

def fetch_rain(client,today,base_dir=BASE_DIR):
 # ERA5 reanalysis is typically published with a few days delay. We leave 8 days.
 finish=today-timedelta(days=8);start=finish-timedelta(days=29)
 entries=[];errors=[];network_errors=0
 for area,name,lat,lon in SENTINELS:
  url='https://archive-api.open-meteo.com/v1/archive'
  try:
   raw=client.get(url,{'latitude':lat,'longitude':lon,'start_date':start.isoformat(),'end_date':finish.isoformat(),'daily':'precipitation_sum','timezone':'UTC','models':'era5'})
   actual=recent_rain(raw);normal=get_normal(client,area,lat,lon,base_dir)
   anomaly=rain_anomaly(actual,normal)
   entries.append({'id':area,'name':name,'lat':lat,'lon':lon,'date':finish.isoformat(),'period_start':start.isoformat(), 'unit':'mm/30 jours', 'source_url':url+'?'+urlencode({'latitude':lat,'longitude':lon,'start_date':start.isoformat(),'end_date':finish.isoformat(),'daily':'precipitation_sum','timezone':'UTC','models':'era5'}),**anomaly})
  except Exception as e:
   errors.append(f'{area}: {str(e)[:110]}')
   if isinstance(e,(requests.ConnectionError,requests.Timeout,ConnectionError,TimeoutError)):
    network_errors+=1
    if network_errors>=2:break
 if not entries:raise ValueError('Aucun point ERA5 valide ; '+('; '.join(errors[:2])))
 return entries,errors

def last_geometry(event):
 geom=event.get('geometry',[])
 if not isinstance(geom,list) or not geom:return None
 # NASA EONET returns last geometry chronological snapshot; for polygons use first ring centroid approximation.
 for g in reversed(geom):
  c=g.get('coordinates');kind=g.get('type')
  if kind=='Point' and isinstance(c,list) and len(c)>=2 and all(number(v) is not None for v in c[:2]):return c[:2],short_date(g.get('date'))
  if kind=='Polygon' and isinstance(c,list) and c and isinstance(c[0],list):
   ring=[p for p in c[0] if isinstance(p,list) and len(p)>=2 and all(number(v) is not None for v in p[:2])]
   if ring:return [sum(p[0] for p in ring)/len(ring),sum(p[1] for p in ring)/len(ring)],short_date(g.get('date'))
 return None

def fetch_eonet(client,cat):
 if cat not in ('wildfires','floods'):raise ValueError('Catégorie non reconnue')
 url='https://eonet.gsfc.nasa.gov/api/v3/events'
 data=client.get(url,{'category':cat,'days':45,'status':'all','limit':250})
 raw=data.get('events')
 if not isinstance(raw,list):raise ValueError('Liste events absente')
 out=[]
 for ev in raw:
  point=last_geometry(ev)
  if point is None:continue
  (lon,lat),when=point
  if not (-180<=lon<=180 and -90<=lat<=90):continue
  links=ev.get('sources') if isinstance(ev.get('sources'),list) else []
  source_url=next((s['url'] for s in links if isinstance(s,dict) and str(s.get('url','')).startswith('https://')),url)
  out.append({'id':str(ev.get('id') or '')[:100],'title':str(ev.get('title') or 'Événement recensé')[:170], 'lat':round(lat,4),'lon':round(lon,4), 'date':when, 'url':source_url, 'category':cat})
 return out[:150],[]

def fetch_prices(client,today):
 cutoff=(today-timedelta(days=180)).isoformat()
 out=[];warnings=[];network_errors=0
 for country,label in MARKET_COUNTRIES:
  url='https://fdw.fews.net/api/marketpricefacts.json'
  try:
   data=client.get(url,{'country_code':country,'start_date':cutoff,'page_size':100,'offset':0})
   rr=rows(data);good=[]
   for o in rr:
    if not isinstance(o,dict):continue
    val=number(grab(o,'value','price','price_value'))
    at=short_date(grab(o,'period_date','date','start_date'))
    unit=grab(o,'unit','price_unit');cur=grab(o,'currency')
    product=grab(o,'product','commodity','marketproduct')
    market=grab(o,'market','market_name')
    if val is None or val<0 or not at or not unit or not cur or not product or not market:continue
    good.append({'country':country,'country_label':label,'commodity':str(product)[:120], 'market':str(market)[:120], 'date':at, 'price':val,'unit':str(unit)[:50],'currency':str(cur)[:30], 'url':url+'?'+urlencode({'country_code':country,'start_date':cutoff})})
   out.extend(sorted(good,key=lambda x:x['date'],reverse=True)[:8])
   if not good:warnings.append(label+': aucun enregistrement exploitable dans la première page')
   if isinstance(data,dict) and data.get('next'):warnings.append(label+': échantillon partiel (pagination disponible)')
  except Exception as e:
   warnings.append(label+': '+str(e)[:110])
   if isinstance(e,(requests.ConnectionError,requests.Timeout,ConnectionError,TimeoutError)):
    network_errors+=1
    if network_errors>=2:break
 if not out:raise ValueError('Aucun prix exploitable ; '+('; '.join(warnings[:2])))
 return sorted(out,key=lambda x:x['date'],reverse=True)[:60], warnings

def fetch_health(client,today):
 url='https://www.who.int/api/news/dons'
 data=client.get(url,{'$top':80,'$orderby':'PublicationDate desc'})
 raw=rows(data)
 items=[];start=today-timedelta(days=150)
 for o in raw:
  if not isinstance(o,dict):continue
  at=short_date(grab(o,'PublicationDateAndTime','PublicationDate','DateCreated'))
  title=grab(o,'Title')
  if not at or not title or date.fromisoformat(at)<start:continue
  path=grab(o,'ItemDefaultUrl')
  link='https://www.who.int'+path if isinstance(path,str) and path.startswith('/') else 'https://www.who.int/emergencies/disease-outbreak-news'
  items.append({'date':at,'title':str(title)[:220],'url':link,'category':'sanitaire','status':'bulletin OMS — lien causal non établi'})
 return sorted(items,key=lambda x:x['date'],reverse=True)[:35],[]

def fetch_displacement(client,today):
 url='https://api-data.unhcr.org/api/v0/data/PopStatsOfficial'
 out=[];warnings=[]
 for iso,label in UNHCR_COUNTRIES:
  try:
   # REF_UNHCR = refugees under UNHCR mandate, by country of asylum.
   # Do not interpret as newly displaced, internally displaced or climate-displaced.
   data=client.get(url,{'c[pop_type]':'REF_UNHCR','c[ref_area_adm0]':iso,'time_period':'ge:2024','dimensions':'pop_type,ref_area_adm0,sex','page_size':100})
   rr=rows(data)
   candidates=[]
   for o in rr:
    if not isinstance(o,dict):continue
    s=grab(o,'sex')
    if str(s) not in ('_T','Total','TOTAL'):continue
    at=str(grab(o,'time_period') or '')[:7];value=number(grab(o,'obs_value','value'))
    if len(at)==7 and value is not None and value>=0:candidates.append((at,int(value)))
   if candidates:
    at,value=max(candidates)
    out.append({'country':iso,'country_label':label,'date':at, 'value':value, 'unit':'réfugiés enregistrés (pays d’asile)', 'url':url+'?'+urlencode({'c[pop_type]':'REF_UNHCR','c[ref_area_adm0]':iso,'time_period':'ge:2024','dimensions':'pop_type,ref_area_adm0,sex'})})
   else:warnings.append(label+': total (sex=_T) absent ou pagination incomplète')
  except Exception as e:warnings.append(label+': '+str(e)[:100])
 if not out:raise ValueError('HCR: pas de séries retenues ; '+('; '.join(warnings[:2])))
 return out,warnings

def empty_state():
 return {'schema':2,'initialized_at':date.today().isoformat(),'checked_at':None,'sources':SOURCES,'datasets':{key:{'status':'jamais collecté' if key!='harvests' else 'validation humaine requise','checked_at':None,'as_of':None,'records':[], 'warnings':[],'error':None} for key in SOURCES}, 'alerts':[], 'notes':['Observations d’origines différentes, sans attribution automatique à ENSO.','Les données en blanc ne sont pas égales à zéro.']}

def write_json(path,content):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False,suffix='.tmp') as f:
  json.dump(content,f,ensure_ascii=False,indent=2,allow_nan=False);tmp=f.name
 os.replace(tmp,path)

def derive_alerts(datasets):
 """Monitoring thresholds are user-configurable rules, NOT official disaster forecasts."""
 out=[]
 for rec in datasets.get('rain',{}).get('records',[]):
  if rec.get('flag') in ('déficit marqué à examiner','excédent marqué à examiner'):
   out.append({'id':'rain-'+rec['id']+'-'+rec['date'],'date':rec['date'],'domain':'climat','level':'à vérifier','place':rec['name'],'message':rec['flag']+' ('+str(rec['anomaly_pct'])+' % sur 30 jours)','source_url':rec['source_url'],'method':'Seuil de veille technique : écart ≥40 % en déficit ou ≥50 % en excédent, avec volumes minimaux ; non attribué à ENSO'})
 return out[:50]

def collect(current=None,client=None,today=None,base_dir=BASE_DIR):
 now=datetime.now(timezone.utc).isoformat(timespec='seconds')
 today=today or date.today();client=client or APIClient();current=current or empty_state()
 obj=json.loads(json.dumps(current))
 obj['schema']=2;obj['sources']=SOURCES;obj.setdefault('datasets',{})
 plans=[('rain',lambda:fetch_rain(client,today,base_dir)),('fires',lambda:fetch_eonet(client,'wildfires')),('floods',lambda:fetch_eonet(client,'floods')),('prices',lambda:fetch_prices(client,today)),('health',lambda:fetch_health(client,today)),('displacement',lambda:fetch_displacement(client,today))]
 for sid,fn in plans:
  old=obj['datasets'].get(sid,{})
  try:
   records,warnings=fn()
   when=max((r.get('date') or '' for r in records),default=None)
   obj['datasets'][sid]={'status':'ok','checked_at':now,'as_of':when,'records':records,'count':len(records),'warnings':warnings[:20], 'error':None}
  except Exception as e:
   # Previous verified observations retained, but status becomes explicit stale/error.
   obj['datasets'][sid]={**old,'status':'échec — anciennes données conservées' if old.get('checked_at') else 'indisponible','last_attempt':now,'error':str(e)[:280]}
 obj['datasets'].setdefault('harvests',{'status':'validation humaine requise','checked_at':None,'as_of':None,'records':[], 'warnings':[],'error':None})
 obj['checked_at']=now
 obj['alerts']=derive_alerts(obj['datasets'])
 return obj

def main():
 p=argparse.ArgumentParser(description='Collecte sectorielle indépendante ENSO / ATLAS')
 p.add_argument('--file',default=str(FILE));p.add_argument('--dry-run',action='store_true')
 args=p.parse_args();path=Path(args.file)
 try:current=json.loads(path.read_text(encoding='utf-8'))
 except FileNotFoundError:current=empty_state()
 state=collect(current)
 if not args.dry_run:write_json(path,state)
 for name,data in state['datasets'].items():print(f"{name:14} {data['status']:<36} {len(data.get('records',[])):3} ligne(s) | {data.get('error') or ''}")
 print('Alertes (à vérifier) :',len(state['alerts']))

if __name__=='__main__':main()
