"""Historisation SQLite réversible des observations externes.

Chaque (source, sujet, date observée) garde la dernière version et
l'historique d'ingestion (révisions incluses) grâce à une empreinte de contenu.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

DEFAULT_DB=Path(__file__).resolve().parent/'data'/'history.sqlite'

def save_history(feeds,db=DEFAULT_DB):
 Path(db).parent.mkdir(parents=True,exist_ok=True)
 with sqlite3.connect(db) as cn:
  cn.execute('''CREATE TABLE IF NOT EXISTS observations (
   id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, subject TEXT NOT NULL,
   observation_date TEXT NOT NULL, ingested_at TEXT NOT NULL, value REAL,
   unit TEXT, payload TEXT NOT NULL, checksum TEXT NOT NULL,
   UNIQUE (source, subject, observation_date, checksum)
  )''')
  cn.execute('CREATE INDEX IF NOT EXISTS obs_lookup ON observations (source, subject, observation_date)')
  now=datetime.now(timezone.utc).isoformat(timespec='seconds')
  count=0
  for source in ('rain','fires','floods','prices','health','displacement'):
   for rec in feeds.get('datasets',{}).get(source,{}).get('records',[]):
    observed=str(rec.get('date') or '')[:10]
    if not observed:continue
    if source=='rain':subject=rec.get('id');value=rec.get('anomaly_pct');unit='% / 30 j'
    elif source=='prices':subject='|'.join(str(rec.get(key,'')) for key in ('country','market','commodity','currency','unit'));value=rec.get('price');unit=str(rec.get('currency',''))+'/'+str(rec.get('unit',''))
    elif source=='displacement':subject=rec.get('country');value=rec.get('value');unit='personnes'
    else:subject=rec.get('id') or rec.get('title');value=None;unit=None
    if not subject:continue
    raw=json.dumps(rec,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    check=hashlib.sha256(raw.encode('utf-8')).hexdigest()
    cur=cn.execute('INSERT OR IGNORE INTO observations (source,subject,observation_date,ingested_at,value,unit,payload,checksum) VALUES (?,?,?,?,?,?,?,?)',(source,str(subject),observed,now,value,unit,raw,check))
    count+=cur.rowcount
 return count

def query_history(source,subject=None,db=DEFAULT_DB,limit=150):
 if source not in ('rain','fires','floods','prices','health','displacement'):raise ValueError('Flux inconnu')
 limit=min(500,max(1,int(limit)))
 if not Path(db).exists():return []
 sql='SELECT source,subject,observation_date,ingested_at,value,unit,checksum FROM observations WHERE source=?'
 args=[source]
 if subject is not None:
  sql+=' AND subject=?';args.append(subject)
 sql+=' ORDER BY observation_date DESC, ingested_at DESC LIMIT ?';args.append(limit)
 with sqlite3.connect(db) as cn:
  cn.row_factory=sqlite3.Row
  return [dict(r) for r in cn.execute(sql,args).fetchall()]
