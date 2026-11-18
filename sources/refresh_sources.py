#!/usr/bin/env python3
"""Collecte prudente de données publiques officielles.

La collecte climatique vérifie NOAA/CPC (RONI, ENSO phases, bulletin) et FAO (FFPI).
Les synthèses de risques pays, événements de veille et scénarios restent validés
manuellement ; aucune génération automatique de fausses données d'impact.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile

import requests
from bs4 import BeautifulSoup

HERE=Path(__file__).resolve().parent
DATA_FILE=HERE/'data'/'state.json'
LOG_FILE=HERE/'data'/'collecte.log'
SOURCES={
    'noaa-disc':'https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.html',
    'noaa-roni':'https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/',
    'noaa-prob':'https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/probabilities/',
    'fao':'https://www.fao.org/worldfoodsituation/foodpricesindex/en/',
}
HEADERS={'User-Agent':'ENSO-Atlas/0.1 (+local scientific monitoring, reasonable access)',
         'Accept':'text/html,application/xhtml+xml'}
MONTHS={'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,'july':7,
        'august':8,'september':9,'october':10,'november':11,'december':12}

def fetch(url):
    r=requests.get(url,headers=HEADERS,timeout=(8,24));r.raise_for_status()
    if len(r.content)<100: raise ValueError('Réponse trop courte')
    return r.text

def table_rows(soup):
    for tr in soup.select('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'],recursive=False)]
        if cells: yield cells

def official_date(text):
    # Includes NOAA's "12 November 2026" and "Issued November 2026".
    m=re.search(r'\b(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(20\d{2})\b',text,re.I)
    if not m:return None
    return f'{int(m.group(3)):04d}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}'

def parse_roni(html, previous):
    soup=BeautifulSoup(html,'html.parser')
    latest=[]
    for row in table_rows(soup):
        if not row or not re.fullmatch(r'20\d{2}',row[0].strip()):continue
        vals=[]
        for cell in row[1:]:
            try: vals.append(float(cell))
            except ValueError: pass
        if 1<=len(vals)<=12:latest.append((int(row[0]),vals))
    if not latest:
        text=soup.get_text('\n',strip=True)
        for line in text.splitlines():
            m=re.match(r'^\s*(20\d{2})\s+((?:[+\-]?\d+(?:\.\d+)?\s*){1,12})\s*$',line)
            if m:
                vals=[float(x) for x in m.group(2).split()]
                if len(vals)<=12:latest.append((int(m.group(1)),vals))
    if not latest: raise ValueError('RONI: aucune série annuelle reconnue')
    year,values=max(latest,key=lambda v:v[0]);
    if year<2026 or values[-1] < -6 or values[-1] > 6:raise ValueError('RONI: série anormale')
    seasons=['DJF','JFM','FMA','MAM','AMJ','MJJ','JJA','JAS','ASO','SON','OND','NDJ']
    new=deepcopy(previous)
    new.update({'year':year,'periods':seasons[:len(values)],'values':values,
       'description':'RONI trimestriel mobile, NOAA/CPC ERSSTv6, référence 1991–2020'})
    return new

def parse_probabilities(html,previous):
    soup=BeautifulSoup(html,'html.parser')
    out=[]
    for row in table_rows(soup):
        if not row:continue
        first=row[0].strip().upper()
        m=re.search(r'\b(DJF|JFM|FMA|MAM|AMJ|MJJ|JJA|JAS|ASO|SON|OND|NDJ)\b',first)
        if not m:continue
        try:nums=[int(re.sub(r'[^0-9]','',v)) for v in row[-3:]]
        except (ValueError,IndexError):continue
        if len(nums)!=3 or any(not 0<=n<=100 for n in nums) or sum(nums) not in (99,100,101):continue
        out.append((m.group(1),*nums))
    if len(out)<5:
        txt=soup.get_text(' ',strip=True)
        p=re.compile(r'\b(DJF|JFM|FMA|MAM|AMJ|MJJ|JJA|JAS|ASO|SON|OND|NDJ)\b\s+(?:[A-Za-z]{3,9}\s+){0,3}(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})',re.I)
        for m in p.finditer(txt):
            nums=list(map(int,m.groups()[1:]));
            if sum(nums) in (99,100,101):out.append((m.group(1).upper(),*nums))
    clean=[];seen=set()
    for r in out:
        if r[0] not in seen:clean.append(r);seen.add(r[0])
    if len(clean)<5:raise ValueError('NOAA ENSO: table de probabilités non reconnue')
    # Keep direct official seasonal abbreviations; the issuance date is displayed separately.
    ret=deepcopy(previous)
    ret.update({'periods':[r[0] for r in clean[:9]],'la_nina':[r[1] for r in clean[:9]],
        'neutral':[r[2] for r in clean[:9]],'el_nino':[r[3] for r in clean[:9]]})
    issue=re.search(r'Issued\s+([A-Za-z]+)\s+(20\d{2})',soup.get_text(' ',strip=True),re.I)
    if issue:
        mon=MONTHS.get(issue.group(1).lower())
        if mon:ret['date']=f'{issue.group(2)}-{mon:02d}-01'
    return ret

def parse_bulletin(html, state):
    soup=BeautifulSoup(html,'html.parser')
    text=soup.get_text(' ',strip=True).replace('\xa0',' ')
    dt=official_date(text)
    if dt is None:raise ValueError('NOAA bulletin: date introuvable')
    if dt < state['cpc_date']:raise ValueError('NOAA bulletin plus ancien que le snapshot')
    status='El Niño confirmé · suivi' if 'El Niño Advisory' in text or 'El Nino Advisory' in text else None
    if status and re.search(r'continues to strengthen|is strengthening',text,re.I):status='El Niño confirmé · renforcement'
    if status and re.search(r'is weakening|continues to weaken',text,re.I):status='El Niño confirmé · affaiblissement'
    if re.search(r'La Niña Advisory|La Nina Advisory',text):status='La Niña confirmée'
    if re.search(r'ENSO[\s-]*neutral conditions',text,re.I):status=status or 'ENSO neutre'
    changed={ 'cpc_date':dt, 'index':deepcopy(state['index']),
               'strong_probability':deepcopy(state['strong_probability']),
               'historic_probability':deepcopy(state['historic_probability'])}
    if status:changed['status']=status
    # NOAA usually: "+2.5°C in Niño-3.4, +3.6°C in Niño-3, and +4.4°C in Niño-1+2".
    found_index=False
    for key,idx in [('nino34','3.4'),('nino3','3'),('nino12','1\\+2')]:
        m=re.search(r'([+\-]?\d+(?:\.\d+)?)\s*°\s*C\s+in\s+Ni.o-?'+idx+r'(?![.0-9])',text,re.I)
        if m:
            changed['index'][key]=float(m.group(1));found_index=True
    # Retain old index/date if the latest advisory reports no comparable value.
    if found_index:changed['index']['date']=dt
    m=re.search(r'(?:greater than|more than|over|above)\s+(?:a\s+|an\s+)?(\d{1,3})\s*%\s+chance',text,re.I)
    if m and 'strong-to-very strong' in text.lower():
        changed['strong_probability']['min']=int(m.group(1));changed['strong_probability']['label']='>'+m.group(1)+' %';changed['strong_probability']['date']=dt
    m=re.search(r'(?:~|approximately|about|around)\s*(\d{1,3})\s*%\s+for\s+the\s+October',text,re.I)
    if m:
        changed['historic_probability']['value']=int(m.group(1));changed['historic_probability']['label']='≈'+m.group(1)+' %';changed['historic_probability']['date']=dt
    return changed

def parse_fao(html, previous):
    text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
    # Seek a dated FAO news update of an FFPI value; no guess if the site format changes.
    found=re.search(r'(?:FFPI|FAO Food Price Index)[^\n]{0,220}?averaged\s+(\d{2,3}(?:[.,]\d)?)\s+points\s+in\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})',text,re.I)
    if not found:raise ValueError('FAO: indice officiel non identifié')
    month=MONTHS[found.group(2).lower()]
    d=f'{found.group(3)}-{month:02d}'
    if d < previous['date']:raise ValueError('FAO: donnée plus ancienne que le snapshot')
    ret=deepcopy(previous);ret['value']=float(found.group(1).replace(',','.'));ret['date']=d
    # Do not invent a month-on-month change when it is not explicitly published.
    ret['monthly_change_pct']=previous['monthly_change_pct'] if d==previous['date'] else None
    return ret

def refresh(statefile=DATA_FILE,fetch_func=fetch):
    raw=json.loads(Path(statefile).read_text(encoding='utf-8'))
    new=deepcopy(raw)
    failures=[];ok=[];updates=[]
    for sid,url in SOURCES.items():
        try:
            html=fetch_func(url)
            if sid=='noaa-disc':
                for k,v in parse_bulletin(html,new).items():new[k]=v
            elif sid=='noaa-roni':new['roni']=parse_roni(html,new['roni'])
            elif sid=='noaa-prob':new['enso_probs']=parse_probabilities(html,new['enso_probs'])
            elif sid=='fao':new['fao']=parse_fao(html,new['fao'])
            ok.append(sid)
            if sid=='noaa-disc' or sid=='noaa-prob':
                for ev in new.get('sources',[]):
                    if ev['id']==sid and sid=='noaa-disc':ev['date']=new['cpc_date']
                    if ev['id']==sid and sid=='noaa-prob':ev['date']=new['enso_probs']['date']
            if sid=='fao':
                for ev in new.get('sources',[]):
                    if ev['id']==sid:ev['date']=new['fao']['date']+'-01'
        except Exception as e:
            failures.append(f'{sid}: {type(e).__name__} – {str(e)[:180]}')
    today=date.today().isoformat()
    new['last_checked']=today
    new['source_health']={'checked_at':datetime.now(timezone.utc).isoformat(),
        'success':ok,'failed':failures}
    # Updating "updated_at" is permitted only for a successfully read bulletin / new values.
    if ok:
        new['updated_at']=today
        new['snapshot_label']='Dernier relevé source : '+today
    path=Path(statefile)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False,suffix='.tmp') as f:
        json.dump(new,f,ensure_ascii=False,indent=2)
        tmp=f.name
    os.replace(tmp,path)
    message=(f'{len(ok)}/{len(SOURCES)} source(s) analysée(s)' +
             (' ; échecs : '+', '.join(failures) if failures else ' ; collecte réussie'))
    return new,message

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description='Mise à jour prudente NOAA et FAO')
    p.add_argument('--state',default=str(DATA_FILE),help='Chemin du fichier JSON source')
    args=p.parse_args()
    state,message=refresh(Path(args.state))
    print(message)
    print('Dernier relevé :',state['last_checked'])
