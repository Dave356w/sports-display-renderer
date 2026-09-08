"""Render the 7.3-inch e-paper NFC West standings collectible.

NFL companion to render.py. The layout is drawn at 971x1619, populated with
keyless live NFL data, then downsampled to the reTerminal E1002's native
480x800 portrait grid. Set NFL_SAMPLE=1 for the deterministic 2026 Week 1 demo.
"""
from __future__ import annotations
import math, os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'public'/'nfl_nfc_west.png'
TZ=ZoneInfo('America/Los_Angeles')
MASTER=(971,1619); DEVICE=(480,800)
STANDINGS='https://site.api.espn.com/apis/v2/sports/football/nfl/standings'
SCOREBOARD='https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard'
WEST=('SF','SEA','LAR','ARI'); TIE={t:i for i,t in enumerate(WEST)}
INFO={
 'SF':('SAN FRANCISCO','49ERS',(190,25,50),(196,164,92),(255,255,255)),
 'SEA':('SEATTLE','SEAHAWKS',(0,45,78),(93,190,55),(255,255,255)),
 'LAR':('LOS ANGELES','RAMS',(0,53,148),(255,170,0),(255,205,20)),
 'ARI':('ARIZONA','CARDINALS',(151,35,63),(214,184,139),(255,255,255)),
}
WHITE=(255,255,255); NAVY=(4,43,78); RED=(196,30,58); GREY=(120,136,150); BLACK=(20,20,20)
LATO_H='/usr/share/fonts/truetype/lato/Lato-Heavy.ttf'; LATO_B='/usr/share/fonts/truetype/lato/Lato-Bold.ttf'
SERIF='/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Bold.ttf'; FALL='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
ROW=(458,704,950,1196); STAT=(612,746,867)

def F(path,size): return ImageFont.truetype(path if Path(path).exists() else FALL,size)
def http():
 s=requests.Session(); r=Retry(total=3,connect=3,read=3,backoff_factor=.5,status_forcelist=(429,500,502,503,504),allowed_methods=('GET',)); s.mount('https://',HTTPAdapter(max_retries=r)); s.headers['User-Agent']='sports-display-renderer/1.0'; return s

def stat(entry,names,default=None):
 wanted={n.lower() for n in names}
 for x in entry.get('stats',[]):
  keys={str(x.get(k,'')).lower() for k in ('name','abbreviation','shortDisplayName')}
  if keys&wanted:
   if 'divisionrecord' in wanted and x.get('displayValue') is not None: return x['displayValue']
   return x.get('value',x.get('displayValue',default))
 return default

def entries(node):
 if isinstance(node,dict):
  st=node.get('standings')
  if isinstance(st,dict) and isinstance(st.get('entries'),list): yield from st['entries']
  for v in node.values(): yield from entries(v)
 elif isinstance(node,list):
  for v in node: yield from entries(v)

def finalize(rows):
 d={r['abbr']:r for r in rows}
 for a in WEST:d.setdefault(a,{'abbr':a,'wins':0,'losses':0,'ties':0,'div':'0-0'})
 def pct(r):
  g=r['wins']+r['losses']+r['ties']; return (r['wins']+.5*r['ties'])/g if g else 0
 rows=sorted(d.values(),key=lambda r:(-pct(r),-r['wins'],r['losses'],TIE[r['abbr']]))
 lead=rows[0]
 for r in rows:
  r['wl']=f"{r['wins']}-{r['losses']}"+(f"-{r['ties']}" if r['ties'] else '')
  gb=((lead['wins']-r['wins'])+(r['losses']-lead['losses']))/2
  r['gb']='—' if gb<=0 else str(int(gb)) if gb.is_integer() else f'{gb:.1f}'
 return rows

def fetch_standings(year):
 if os.getenv('NFL_SAMPLE')=='1': return finalize([{'abbr':a,'wins':0,'losses':0,'ties':0,'div':'0-0'} for a in WEST])
 j=http().get(STANDINGS,params={'season':year,'seasontype':2},timeout=20); j.raise_for_status(); found=[]; seen=set()
 for e in entries(j.json()):
  a=(e.get('team') or {}).get('abbreviation')
  if a not in WEST or a in seen: continue
  seen.add(a); found.append({'abbr':a,'wins':int(float(stat(e,('wins','w'),0) or 0)),'losses':int(float(stat(e,('losses','l'),0) or 0)),'ties':int(float(stat(e,('ties','t'),0) or 0)),'div':str(stat(e,('divisionrecord','div','division'),'0-0'))})
 return finalize(found)

def sample_games(): return 1,[{'away':'NE','home':'SEA','dt':datetime(2026,9,9,17,20,tzinfo=TZ)},{'away':'SF','home':'LAR','dt':datetime(2026,9,10,17,35,tzinfo=TZ)},{'away':'ARI','home':'LAC','dt':datetime(2026,9,13,13,25,tzinfo=TZ)}]
def fetch_games():
 if os.getenv('NFL_SAMPLE')=='1': return sample_games()
 r=http().get(SCOREBOARD,params={'limit':100},timeout=20); r.raise_for_status(); j=r.json(); week=(j.get('week') or {}).get('number'); games=[]
 for e in j.get('events',[]):
  c=(e.get('competitions') or [{}])[0]; side={x.get('homeAway'):x for x in c.get('competitors',[])}; home=((side.get('home') or {}).get('team') or {}).get('abbreviation'); away=((side.get('away') or {}).get('team') or {}).get('abbreviation')
  if not home or not away or not ({home,away}&set(WEST)): continue
  try: dt=datetime.fromisoformat(e['date'].replace('Z','+00:00')).astimezone(TZ)
  except Exception: continue
  games.append({'away':away,'home':home,'dt':dt})
 u={(g['away'],g['home'],g['dt'].isoformat()):g for g in games}; return week,sorted(u.values(),key=lambda g:g['dt'])
def matchup(g):
 a,h=g['away'],g['home']; return f'{a} @ {h}' if a in WEST else f'{h} vs {a}'

def star(draw,cx,cy,r=17):
 pts=[]
 for i in range(10):
  rr=r if i%2==0 else r*.43; ang=-math.pi/2+i*math.pi/5; pts.append((cx+rr*math.cos(ang),cy+rr*math.sin(ang)))
 draw.polygon(pts,fill=RED)

def border(d):
 d.rectangle((26,26,945,1590),outline=NAVY,width=4); d.rectangle((34,34,937,1582),outline=NAVY,width=2)
 for x,y,sx,sy in ((26,26,1,1),(945,26,-1,1),(26,1590,1,-1),(945,1590,-1,-1)):
  d.line((x,y+sy*30,x+sx*30,y),fill=NAVY,width=4); d.line((x+sx*10,y+sy*40,x+sx*40,y+sy*10),fill=NAVY,width=2)
def header(d,now):
 d.text((486,111),'NFC WEST',font=F(SERIF,118),fill=NAVY,anchor='mm')
 mini=F(LATO_B,18)
 for i,s in enumerate(('NFL','NATIONAL','FOOTBALL','CONFERENCE')): d.text((54,92+i*27),s,font=mini,fill=NAVY)
 for i,s in enumerate(('WEST','DIVISION','STRENGTH','BUILDS','CHAMPIONS')): d.text((916,92+i*26),s,font=mini,fill=NAVY,anchor='ra')
 d.line((170,226,366,208),fill=RED,width=5); d.line((606,208,801,226),fill=RED,width=5)
 for x in (405,445,486,527,567): star(d,x,215,14)
 d.text((486,273),now.strftime('%B %-d, %Y').upper(),font=F(SERIF,39),fill=NAVY,anchor='mm')
 for x,t in zip(STAT,('W-L','DIV','GB')): d.text((x,329),t,font=F(SERIF,34),fill=NAVY,anchor='mm')
 d.line((85,350,912,350),fill=NAVY,width=2)
def icon(d,a,x,y,accent):
 if a=='SF':
  d.line((x+8,y+35,x+8,y+91),fill=accent,width=6); d.line((x+56,y+35,x+56,y+91),fill=accent,width=6); d.arc((x+8,y+25,x+56,y+78),180,360,fill=accent,width=5); d.line((x,y+91,x+65,y+91),fill=accent,width=5)
 elif a=='SEA':
  d.polygon([(x-4,y+90),(x+15,y+62),(x+31,y+82),(x+52,y+56),(x+73,y+90)],fill=(130,150,170)); d.line((x+33,y+28,x+33,y+95),fill=(230,236,240),width=5); d.ellipse((x+17,y+45,x+49,y+55),outline=(230,236,240),width=4)
 elif a=='LAR':
  d.arc((x-2,y+24,x+70,y+100),175,535,fill=accent,width=10); d.arc((x+15,y+44,x+53,y+84),165,500,fill=accent,width=7)
 else:
  d.line((x+13,y+39,x+13,y+98),fill=accent,width=8); d.line((x+13,y+61,x+2,y+53),fill=accent,width=6); d.line((x+13,y+76,x+27,y+64),fill=accent,width=6); d.polygon([(x+26,y+98),(x+50,y+58),(x+76,y+98)],fill=(115,35,45))
def pennant(d,a,cy):
 city,name,primary,accent,ink=INFO[a]; top,bottom=cy-91,cy+91; x0=66
 d.line((58,top-8,58,bottom+8),fill=BLACK,width=13); d.ellipse((49,top-17,67,top+1),fill=BLACK)
 outer=[(x0,top),(430,top+20),(525,cy),(430,bottom-20),(x0,bottom)]; inner=[(x0+10,top+8),(425,top+27),(508,cy),(425,bottom-27),(x0+10,bottom-8)]
 d.polygon(outer,fill=accent); d.polygon(inner,fill=primary); icon(d,a,88,cy-57,accent)
 d.text((205,cy-27),city,font=F(LATO_B,25),fill=ink,anchor='lm'); d.text((205,cy+24),name,font=F(LATO_H,43 if len(name)<9 else 37),fill=ink,anchor='lm')
def rows(d,stand):
 sf=F(SERIF,51)
 for i,(r,cy) in enumerate(zip(stand,ROW)):
  pennant(d,r['abbr'],cy)
  for x,val in zip(STAT,(r['wl'],r['div'],r['gb'])): d.text((x,cy),val,font=sf,fill=NAVY,anchor='mm')
  d.line((85,cy+119,912,cy+119),fill=NAVY,width=2)
def weekbox(d,week,games):
 d.rectangle((52,1332,919,1544),outline=NAVY,width=3); d.rectangle((60,1340,911,1536),outline=NAVY,width=1); d.line((95,1380,310,1380),fill=RED,width=4); d.line((662,1380,877,1380),fill=RED,width=4); d.text((486,1376),'THIS WEEK',font=F(SERIF,44),fill=NAVY,anchor='mm')
 games=games[:4]
 if games:
  left,right=76,895; w=(right-left)/len(games)
  for i,g in enumerate(games):
   cx=left+w*(i+.5)
   if i: sx=int(left+w*i); d.line((sx,1411,sx,1511),fill=GREY,width=2)
   d.text((cx,1443),matchup(g),font=F(LATO_H,30 if len(games)<=3 else 23),fill=NAVY,anchor='mm'); d.text((cx,1494),g['dt'].strftime('%a %-I:%M %p').upper(),font=F(LATO_B,25 if len(games)<=3 else 20),fill=NAVY,anchor='mm')
 else: d.text((486,1465),'SCHEDULE UNAVAILABLE',font=F(LATO_B,27),fill=NAVY,anchor='mm')
 d.text((82,1571),f"WEEK {week or '—'}",font=F(LATO_B,16),fill=NAVY,anchor='lm'); d.line((276,1571,369,1571),fill=RED,width=3); d.text((486,1571),'480 × 800 PORTRAIT',font=F(LATO_B,16),fill=NAVY,anchor='mm'); d.line((602,1571,695,1571),fill=RED,width=3); d.text((888,1571),'FOOTBALL LIVES HERE',font=F(LATO_B,16),fill=NAVY,anchor='rm')
def render(stand,week,games,now):
 im=Image.new('RGB',MASTER,WHITE); d=ImageDraw.Draw(im); border(d); header(d,now); rows(d,stand); weekbox(d,week,games); return im.resize(DEVICE,Image.Resampling.LANCZOS)
def main():
 now=datetime.now(TZ)
 try: stand=fetch_standings(now.year)
 except Exception as e: print('WARNING standings:',e); stand=finalize([{'abbr':a,'wins':0,'losses':0,'ties':0,'div':'0-0'} for a in WEST])
 try: week,games=fetch_games()
 except Exception as e: print('WARNING schedule:',e); week,games=None,[]
 print('NFC West',now.strftime('%Y-%m-%d %H:%M %Z')); [print(f" {r['abbr']} {r['wl']} DIV {r['div']} GB {r['gb']}") for r in stand]
 im=render(stand,week,games,now); OUT.parent.mkdir(parents=True,exist_ok=True); im.save(OUT,optimize=True); print('Wrote',OUT,im.size)
if __name__=='__main__': main()
