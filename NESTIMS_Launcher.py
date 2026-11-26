import tkinter as tk
from tkinter import ttk, messagebox
import os, sys, json, hashlib, threading, queue, subprocess, urllib.request, urllib.error, webbrowser, re, time, shutil, zipfile
from pathlib import Path
from urllib.parse import urlparse

OWNER = 'NebulaStack-prog'
CATALOG_URL = 'https://raw.githubusercontent.com/NebulaStack-prog/NebulaStack-Launcher/main/catalog.json'
BG = '#000000'
FG = '#c0c0c0'
ACCENT = '#55ffff'
MUTED = '#808080'
FONT = ('Consolas', 12)
SMALL = ('Consolas', 10)
DEFAULT_CATALOG = [
    {'id':'NS-002','name':'Friday 13 – I','repo':'Friday-13-I','category':'Games','description':'Classic grid survival with three difficulty levels.'},
    {'id':'NS-004','name':'Friday 13 – II','repo':'Friday-13-II','category':'Games','description':'Endless procedurally generated world.'}
]
ROOT = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'NESTIMS' / 'LauncherAlpha'
ROOT.mkdir(parents=True, exist_ok=True)
STATE = ROOT / 'library.json'
CATALOG_FILE = ROOT / 'catalog.json'
PREFS = ROOT / 'settings.json'
# Release metadata lives in the central catalog, not in the REST API.

def manifest_releases(items):
    releases, errors = {}, {}
    for item in items:
        key, repo = item['id'], item['repo']
        a = item.get('release')
        if a is None:
            a = item.get('jar_release')
        if a is None:
            errors[key] = item.get('release_note') or 'No Windows package listed; synchronize catalog.'
            continue
        if not isinstance(a, dict):
            raise ValueError('Invalid release for '+key)
        name, version, size, digest, url = (a.get(k) for k in ('name','version','size','digest','url'))
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.(exe|jar)',name,re.I):
            raise ValueError('Invalid package name for '+key)
        package_type = a.get('package_type', name.rsplit('.',1)[1].lower())
        if package_type not in ('exe','jar') or not name.lower().endswith('.'+package_type):
            raise ValueError('Invalid package type for '+key)
        if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',version):
            raise ValueError('Invalid release version for '+key)
        if type(size) is not int or not 0 < size <= 2*1024**3:
            raise ValueError('Invalid package size for '+key)
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):
            raise ValueError('Missing or invalid SHA-256 for '+key)
        if url != f'https://github.com/{OWNER}/{repo}/releases/download/{version}/{name}':
            raise ValueError(f'Unexpected release URL for {key}: expected {version}/{name}; received {url!r}')
        releases[key] = dict(version=version,name=name,size=size,digest=digest,url=url,package_type=package_type)
    return releases, errors


def read_manifest():
    if not CATALOG_FILE.exists():
        return DEFAULT_CATALOG
    return json.loads(CATALOG_FILE.read_text(encoding='utf-8-sig'))

def parse_catalog(items):
    if not isinstance(items,list) or len(items)>200: raise ValueError('Catalog must be a JSON array')
    result = {}
    for a in items:
        key, repo = a['id'], a['repo']
        if not re.fullmatch(r'NS-[0-9]{3,6}',key) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*',repo): raise ValueError('Invalid product ID or repository')
        if key in result or a['category'] not in ('Games','Infotech'): raise ValueError('Duplicate ID or invalid category')
        if not all(isinstance(a[k],str) and 0 < len(a[k]) <= 2000 for k in ('name','description')): raise ValueError('Invalid product text')
        result[key] = (a['name'],repo,a['description'],a['category'])
    return result

def load_catalog():
    items = read_manifest()
    catalog = parse_catalog(items)
    manifest_releases(items)
    return catalog

CATALOG_ERROR = None
try: CATALOG = load_catalog()
except Exception as e:
    CATALOG = parse_catalog(DEFAULT_CATALOG); CATALOG_ERROR = str(e)

def read_state():
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        return {k:v for k,v in data.items() if re.fullmatch(r'NS-[0-9]{3,6}',k) and isinstance(v,dict) and isinstance(v.get('version'),str) and isinstance(v.get('sha256'),str) and v.get('package_type','exe') in ('exe','jar')}
    except (OSError, ValueError, AttributeError):
        return {}

def save_state(data):
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(STATE)

def executable(key, package_type='exe'):
    if not re.fullmatch(r'NS-[0-9]{3,6}',key) or package_type not in ('exe','jar'):
        raise ValueError('Invalid package path')
    return ROOT / (key + '.' + package_type)

def java_command():
    # Prefer a console-free Java runtime on Windows; also support JAVA_HOME.
    names = ('javaw.exe','java.exe') if sys.platform=='win32' else ('java',)
    home = os.environ.get('JAVA_HOME')
    for name in names:
        if home:
            candidate = Path(home.strip('"')) / 'bin' / name
            if candidate.is_file(): return str(candidate.resolve())
        candidate = shutil.which(name)
        if candidate: return str(Path(candidate).resolve())
    raise ValueError('Java runtime not found. Install a compatible Java runtime and add Java to PATH or set JAVA_HOME, then restart Launcher.')

def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'NESTIMS-Launcher/3.0-alpha3.5'}), timeout=30)

def fetch_catalog():
    
    url = f'{CATALOG_URL}?sync={time.time_ns()}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'NESTIMS-Launcher/3.0-alpha3.5',
        'Cache-Control': 'no-cache, no-store, max-age=0',
        'Pragma': 'no-cache',
        'Accept': 'application/json, text/plain, */*',
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        if urlparse(response.geturl()).scheme != 'https':
            raise ValueError('Insecure catalog redirect')
        raw = response.read(1024*1024+1)
    if len(raw)>1024*1024:
        raise ValueError('Catalog is too large')
    items = json.loads(raw.decode('utf-8-sig'))
    parse_catalog(items)
    manifest_releases(items)
    return items

def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()  # Build UI before showing any window.
        self.title('NESTIMS Launcher | 3.0 Alpha 3.5')
        self.geometry('1040x720'); self.minsize(800,600)
        self.configure(bg=BG)
        self.library = read_state(); self.releases = {}; self.errors = {};
        try:
            self.releases, self.errors = manifest_releases(read_manifest())
        except (OSError, ValueError, TypeError, KeyError):
            self.releases, self.errors = {}, {}
        self.jobs = queue.Queue(); self.busy = False; self.processes = {}
        self.blackout = tk.BooleanVar(value=True); self.auto_refresh = tk.BooleanVar(value=True); self.backdrop = None
        self.pending_launch = False
        self.booting = False
        self.boot_animation = tk.BooleanVar(value=True)
        self.catalog_status = tk.StringVar(value='CACHE / NOT SYNCED')
        try:
            prefs=json.loads(PREFS.read_text(encoding='utf-8')); self.blackout.set(bool(prefs.get('blackout',True))); self.auto_refresh.set(bool(prefs.get('auto_refresh',True))); self.boot_animation.set(bool(prefs.get('boot_animation',True)))
        except (OSError,ValueError,AttributeError): pass
        self.page = 'Discover'; self.search = tk.StringVar(); self.category = tk.StringVar(value='All')
        style = ttk.Style(self); style.theme_use('clam')
        style.configure('TProgressbar', troughcolor='#171717', background=ACCENT, bordercolor='#333333', lightcolor=ACCENT,darkcolor=ACCENT)
        style.configure('TScrollbar',background='#555555',troughcolor=BG,arrowcolor=FG,bordercolor=BG)
        self.shell = tk.Frame(self,bg=BG); self.shell.pack(fill='both',expand=True,padx=12,pady=10)
        self.label(self.shell,' NESTIMS OS  /  SOFTWARE DISTRIBUTION TERMINAL  /  3.0a3.5 ',bg=FG,fg=BG,font=('Consolas',11,'bold')).pack(fill='x')
        banner = r""" _   _ _____ ____ _____ ___ __  __ ____
| \ | | ____/ ___|_   _|_ _|  \/  / ___|
|  \| |  _| \___ \ | |  | || |\/| \___ \ 
| |\  | |___ ___) || |  | || |  | |___) |
|_| \_|_____|____/ |_| |___|_|  |_|____/"""
        self.label(self.shell,banner,fg=ACCENT,font=('Consolas',15,'bold')).pack(anchor='w',pady=(8,4))
        self.label(self.shell,'C:\\NESTIMS> LAUNCHER.EXE    [SYSTEM ONLINE]',fg=MUTED,font=SMALL).pack(anchor='w',pady=(0,8))
        self.nav = tk.Frame(self.shell,bg='#161616'); self.nav.pack(fill='x')
        self.navbuttons={}
        for i,page in enumerate(('Discover','My Library','Updates','Settings'),1):
            btn=self.button(self.nav,f'F{i} {page.upper()}',lambda p=page:self.navigate(p)); btn.pack(side='left',padx=2,pady=2)
            self.navbuttons[page]=btn
            self.bind(f'<F{i}>',lambda e,p=page:self.navigate(p))
        self.button(self.nav,'F5 SYNC',self.sync_catalog).pack(side='right',padx=2)
        self.bind('<F5>',lambda e:self.sync_catalog())
        self.main = tk.Frame(self.shell,bg=BG,highlightbackground=FG,highlightthickness=1); self.main.pack(fill='both',expand=True,pady=6)
        self.heading=self.label(self.main,'',fg=ACCENT,font=('Consolas',14,'bold')); self.heading.pack(anchor='w',padx=12,pady=(8,4))
        self.bar=tk.Frame(self.main,bg=BG)
        self.label(self.bar,'SEARCH>').pack(side='left',padx=(0,6))
        tk.Entry(self.bar,textvariable=self.search,bg='#171717',fg=ACCENT,insertbackground=ACCENT,relief='flat',font=FONT,highlightthickness=1,highlightbackground=MUTED).pack(side='left',fill='x',expand=True,ipady=4)
        self.filters=tk.Frame(self.bar,bg=BG); self.filters.pack(side='right',padx=5)
        self.filterbuttons={}
        for cat in ('All','Games','Infotech'):
            b=self.button(self.filters,cat.upper(),lambda c=cat:self.category.set(c)); b.pack(side='left',padx=2); self.filterbuttons[cat]=b
        self.content_host=tk.Frame(self.main,bg=BG); self.content_host.pack(fill='both',expand=True,padx=12,pady=5)
        self.canvas=tk.Canvas(self.content_host,bg=BG,highlightthickness=0); self.canvas.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(self.content_host,orient='vertical',command=self.canvas.yview); scroll.pack(side='right',fill='y'); self.canvas.configure(yscrollcommand=scroll.set)
        self.content=tk.Frame(self.canvas,bg=BG); window=self.canvas.create_window((0,0),window=self.content,anchor='nw')
        self.content.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(window,width=e.width))
        self.bind_all('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units') if not self.backdrop else None)
        self.status=tk.StringVar(value='READY'); self.label(self.shell,textvariable=self.status,fg=FG,wraplength=950,justify='left').pack(anchor='w')
        self.progress=ttk.Progressbar(self.shell,maximum=100); self.progress.pack(fill='x',pady=4)
        self.label(self.shell,' F1 DISCOVER | F2 LIBRARY | F3 UPDATES | F4 SETUP | F5 SYNC ',bg=FG,fg=BG,font=SMALL).pack(fill='x')
        self.search.trace_add('write',lambda *_:self.render()); self.category.trace_add('write',lambda *_:self.render())
        self.protocol('WM_DELETE_WINDOW',self.close); self.after(100,self.poll); self.render()
        if CATALOG_ERROR: self.status.set('Catalog cache error: '+CATALOG_ERROR)
        if self.boot_animation.get(): self.start_boot()
        else:
            self.show_maximized()
            self.finish_boot()

    def show_maximized(self):
        self.attributes('-fullscreen',False)
        if sys.platform=='win32':
            self.state('zoomed')
        else:
            self.geometry(f'{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0')
        self.deiconify()

    def start_boot(self):
        
        self.booting = True
        self.boot_started = None
        self.boot_ready_frames = 0
        self.boot_wait_started = time.monotonic()
        self.boot_after = None
        self.boot_screen = tk.Canvas(self, bg='#000000', highlightthickness=0)
        self.shell.pack_forget()
        self.boot_screen.pack(fill='both', expand=True)
        self.bind('<Escape>', self.finish_boot)
        self.bind('<Return>', self.finish_boot)
        self.bind('<space>', self.finish_boot)
        
        self.geometry(f'{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0')
        self.attributes('-fullscreen',True)
        self.deiconify()
        self.boot_after = self.after(50,self.wait_boot_ready)

    def wait_boot_ready(self):
        if not self.booting: return
        c = self.boot_screen
        target_w, target_h = self.winfo_screenwidth(), self.winfo_screenheight()
        ready = (c.winfo_ismapped() and c.winfo_width() >= target_w-8
                 and c.winfo_height() >= target_h-8)
        self.boot_ready_frames = self.boot_ready_frames+1 if ready else 0
        if self.boot_ready_frames >= 2 or (c.winfo_ismapped() and time.monotonic()-self.boot_wait_started >= 2.5):
            self.boot_started = time.monotonic()
            self.boot_tick()
        else:
            self.boot_after = self.after(50,self.wait_boot_ready)

    def boot_tick(self):
        if not self.booting: return
        t = time.monotonic() - self.boot_started
        if t >= 4.3:
            self.finish_boot()
            return
        c = self.boot_screen
        w, h = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        c.delete('all')
        
        if t < 0.48:
            q = max(0.0, min(1.0, (t - 0.08) / 0.40))
            half = max(1, int(h * 0.48 * q**3))
            c.create_rectangle(12, h//2-half, w-12, h//2+half,
                               fill='#101c1c', outline='')
            c.create_line(12, h//2, w-12, h//2, fill='#75aaaa', width=2)
        else:
            
            for y in range(0,h,5):
                c.create_line(0,y,w,y,fill='#080d0d')
            c.create_rectangle(16,16,w-16,h-16,outline='#315050')
            c.create_text(34,34,anchor='nw',text='NESTIMS SYSTEM BIOS  /  BOOT SEQUENCE',fill=ACCENT,font=('Consolas',14,'bold'))
            c.create_text(34,62,anchor='nw',text='NS-019   |   SOFTWARE DISTRIBUTION TERMINAL   |   3.0',fill=MUTED,font=SMALL)
            c.create_line(34,91,w-34,91,fill='#315050')
            steps = [(0.55,'[ OK ]  TERMINAL DISPLAY INITIALIZED'),
                     (0.85,'[ OK ]  LOCAL STORAGE MOUNTED'),
                     (1.15,'[ OK ]  LIBRARY INDEX LOADED'),
                     (1.45,'[ OK ]  CATALOG CACHE INITIALIZED'),
                     (1.75,'[ OK ]  CATALOG MODULE READY')]
            for i,(at,line) in enumerate(steps):
                if t >= at:
                    c.create_text(34,108+i*23,anchor='nw',text=line,fill=FG,font=FONT)
            logo = ' _   _ _____ ____ _____ ___ __  __ ____\n| \\ | | ____/ ___|_   _|_ _|  \\/  / ___|\n|  \\| |  _| \\___ \\ | |  | || |\\/| \\___ \\ \n| |\\  | |___ ___) || |  | || |  | |___) |\n|_| \\_|_____|____/ |_| |___|_|  |_|____/' .splitlines()
            if t >= 1.7:
                size = max(12,min(25,int((w-90)/27)))
                logo_y = max(258,int(h*0.40))
                for i,line in enumerate(logo):
                    visible = max(0, min(len(line), int((t-1.7)*80)-i*5))
                    c.create_text(w//2,logo_y+i*(size+4),anchor='n',text=line[:visible].ljust(len(line)),fill=ACCENT,font=('Consolas',size,'bold'))
            if t >= 2.4:
                progress = min(1,(t-2.4)/1.15)
                bw=min(520,w-90); x=(w-bw)//2; y=h-108
                c.create_text(x,y-25,anchor='w',text='STARTING NESTIMS LAUNCHER...',fill=FG,font=FONT)
                c.create_rectangle(x,y,x+bw,y+18,outline=MUTED)
                for i in range(40):
                    if i < int(progress*40):
                        c.create_rectangle(x+3+i*(bw-6)/40,y+3,x+3+(i+0.8)*(bw-6)/40,y+15,fill=ACCENT,outline='')
                c.create_text(x+bw,y+28,anchor='ne',text=f'{int(progress*100):03d}%',fill=FG,font=SMALL)
            c.create_text(w//2,h-31,anchor='s',text='ESC / ENTER / SPACE  -  SKIP BOOT',fill=MUTED,font=SMALL)
        self.boot_after = self.after(35,self.boot_tick)

    def finish_boot(self, event=None):
        if self.booting:
            self.booting = False
            if self.boot_after is not None:
                self.after_cancel(self.boot_after)
                self.boot_after = None
            for key in ('<Escape>', '<Return>', '<space>'):
                self.unbind(key)
            self.boot_screen.destroy()
            self.show_maximized()
            self.shell.pack(fill='both',expand=True,padx=12,pady=10)
            self.render()
        if self.auto_refresh.get(): self.after(150,self.sync_catalog)
        return 'break'

    def label(self,parent,text='',bg=BG,fg=FG,font=FONT,**kw):
        return tk.Label(parent,text=text,bg=bg,fg=fg,font=font,anchor='w',**kw)
    def button(self,parent,text,command):
        return tk.Button(parent,text='[ '+text+' ]',command=command,bg=BG,fg=FG,activebackground=ACCENT,activeforeground=BG,relief='flat',bd=0,font=SMALL,padx=5,pady=5,cursor='hand2',highlightthickness=0)
    def navigate(self,page):
        if self.backdrop is not None or self.booting: return
        self.page=page; self.search.set(''); self.category.set('All'); self.canvas.yview_moveto(0); self.render()
    def render(self):
        self.heading.config(text='> '+self.page.upper()+' / '+('REMOTE CATALOG' if self.page=='Discover' else 'LOCAL SYSTEM'))
        for page,btn in self.navbuttons.items(): btn.configure(bg=ACCENT if page==self.page else BG,fg=BG if page==self.page else FG)
        for cat,btn in self.filterbuttons.items(): btn.configure(bg=FG if cat==self.category.get() else BG,fg=BG if cat==self.category.get() else FG)
        for w in self.content.winfo_children(): w.destroy()
        if self.page in ('Settings','Updates'):
            self.bar.pack_forget()
        elif not self.bar.winfo_manager(): self.bar.pack(fill='x',padx=12,pady=5,before=self.content_host)
        if self.page=='Settings': self.render_settings(); return
        count=0
        if self.page=='Updates': self.button(self.content,'CHECK CATALOG + RELEASES',self.sync_catalog).pack(anchor='w',pady=4)
        for key,(name,repo,desc,category) in CATALOG.items():
            installed=self.library.get(key)
            if installed and not executable(key,installed.get('package_type','exe')).is_file(): installed=None
            release=self.releases.get(key)
            update=installed and release and (installed['version']!=release['version'] or installed.get('package_type','exe')!=release['package_type'])
            if self.page in ('Discover','My Library') and (self.category.get() not in ('All',category) or self.search.get().casefold() not in (name+' '+key).casefold()): continue
            if self.page=='My Library' and not installed: continue
            if self.page=='Updates' and not update: continue
            count+=1
            card=tk.Frame(self.content,bg=BG); card.pack(fill='x',pady=3)
            self.label(card,f'{count:02d}  {name.upper()}',fg=ACCENT,font=('Consolas',13,'bold')).pack(anchor='w')
            self.label(card,f'    {key} | {category.upper()} | '+(release['version']+' | '+release['package_type'].upper() if release else 'REMOTE VERSION UNKNOWN'),font=SMALL).pack(anchor='w')
            if installed: self.label(card,'    LOCAL: '+installed['version']+('  [UPDATE AVAILABLE]' if update else '  [INSTALLED]'),font=SMALL).pack(anchor='w')
            self.label(card,'    '+desc,fg=MUTED,font=SMALL,wraplength=680,justify='left').pack(anchor='w',pady=3)
            if self.errors.get(key): self.label(card,'    ! '+self.errors[key],fg='#ff8888',font=SMALL,wraplength=680,justify='left').pack(anchor='w')
            row=tk.Frame(card,bg=BG); row.pack(fill='x',padx=16)
            if installed:
                self.button(row,'RUN',lambda k=key:self.play(k)).pack(side='left')
                self.button(row,'REMOVE',lambda k=key:self.uninstall(k)).pack(side='left')
            if release and (not installed or update): self.button(row,'UPDATE' if update else 'INSTALL',lambda k=key:self.install(k)).pack(side='left')
            self.button(row,'REPOSITORY',lambda r=repo:webbrowser.open(f'https://github.com/{OWNER}/{r}')).pack(side='right')
            tk.Frame(card,bg='#444444',height=1).pack(fill='x',pady=(4,2))
        if not count: self.label(self.content,'-- NO UPDATES FOUND --' if self.page=='Updates' else '-- NO PRODUCTS IN THIS VIEW --',fg=MUTED).pack(anchor='w',pady=20)
    def render_settings(self):
        self.label(self.content,'[ DISPLAY / STARTUP ]',fg=ACCENT).pack(anchor='w',pady=5)
        for title,var in [('Fullscreen black background while playing',self.blackout),('Synchronize catalog and releases at startup',self.auto_refresh),('Play retro startup animation',self.boot_animation)]:
            tk.Checkbutton(self.content,text=title,variable=var,command=self.save_preferences,bg=BG,fg=FG,selectcolor=BG,activebackground=BG,activeforeground=ACCENT,font=FONT,anchor='w').pack(anchor='w')
        self.label(self.content,'[ CENTRAL CATALOG ]',fg=ACCENT).pack(anchor='w',pady=(12,3))
        self.label(self.content,textvariable=self.catalog_status,font=SMALL,wraplength=680,justify='left').pack(anchor='w')
        self.label(self.content,CATALOG_URL,font=SMALL,wraplength=680,justify='left').pack(anchor='w')
        self.button(self.content,'SYNC NOW',self.sync_catalog).pack(anchor='w')
        self.button(self.content,'OPEN CATALOG ON GITHUB',lambda:webbrowser.open(f'https://github.com/{OWNER}/NebulaStack-Launcher/blob/main/catalog.json')).pack(anchor='w')
        self.label(self.content,'[ LOCAL STORAGE ]',fg=ACCENT).pack(anchor='w',pady=(12,3))
        self.label(self.content,str(ROOT),font=SMALL,wraplength=680,justify='left').pack(anchor='w')
        self.button(self.content,'OPEN STORAGE',lambda:self.open_path(ROOT)).pack(anchor='w')
        self.label(self.content,'Stable releases from central catalog. Local cache is used when offline.',font=SMALL).pack(anchor='w',pady=6)
    def save_preferences(self):
        try: PREFS.write_text(json.dumps({'blackout':self.blackout.get(),'auto_refresh':self.auto_refresh.get(),'boot_animation':self.boot_animation.get()},indent=2),encoding='utf-8')
        except OSError as e: messagebox.showerror('Settings',str(e))
    def open_path(self,path):
        try:
            if sys.platform=='win32':
                if path==CATALOG_FILE: subprocess.Popen(['notepad.exe',str(path)])
                else: os.startfile(str(path))
            else: webbrowser.open(path.as_uri())
        except OSError as e: messagebox.showerror('Open failed',str(e))
    def sync_catalog(self):
        if self.booting or self.busy or self.pending_launch or self.processes: return
        old=dict(CATALOG); installed=set(self.library)
        self.status.set('Checking central catalog...'); self.catalog_status.set('SYNC IN PROGRESS')
        def work():
            try:
                items=fetch_catalog(); catalog=parse_catalog(items)
                
                for key in installed:
                    if key in old:
                        if key in catalog and catalog[key][1]!=old[key][1]: raise ValueError('Installed product repository changed: '+key)
                        if key not in catalog:
                            name,repo,desc,category=old[key]
                            items.append(dict(id=key,name=name,repo=repo,description=desc,category=category,release=None,release_note='Retired from central catalog. Installed copy remains available.'))
                catalog=parse_catalog(items)
                releases, errors=manifest_releases(items)
                tmp=CATALOG_FILE.with_suffix('.tmp'); tmp.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(CATALOG_FILE)
                self.jobs.put(('manifest',(catalog,releases,errors,'SYNC OK / DIRECT RELEASES')))
            except Exception as e:
                
                self.jobs.put(('catalog_error','CACHE MODE / '+str(e)))

        self.task(work)
    def show_backdrop(self):
        if not self.blackout.get(): return
        self.saved_geometry=self.geometry(); self.saved_state=self.state()
        self.shell.pack_forget()
        
        self.backdrop=tk.Frame(self,bg='black',cursor='none'); self.backdrop.pack(fill='both',expand=True)
        self.attributes('-topmost',False); self.attributes('-fullscreen',True)
        self.bind('<Escape>',lambda e:self.hide_backdrop())
        self.update_idletasks(); self.lift()
    def hide_backdrop(self):
        if self.backdrop is not None:
            self.backdrop.destroy(); self.backdrop=None
            self.attributes('-fullscreen',False); self.unbind('<Escape>')
            self.geometry(self.saved_geometry)
            if self.saved_state=='zoomed' and sys.platform=='win32': self.state('zoomed')
            self.shell.pack(fill='both',expand=True,padx=12,pady=10); self.lift()
    def task(self,fn):
        if self.busy: return
        self.busy=True; self.progress['value']=0
        def worker():
            try: fn()
            except Exception as e: self.jobs.put(('error',str(e)))
            finally: self.jobs.put(('done',None))
        threading.Thread(target=worker,daemon=True).start()
    def refresh(self):
        self.sync_catalog()
    def install(self,key):
        if self.busy or self.pending_launch or self.is_running(key): return
        a=self.releases[key].copy()
        if a['package_type']=='exe' and sys.platform!='win32': messagebox.showinfo('Windows only','This package requires Windows.'); return
        if not messagebox.askyesno('Confirm download',f"Download {a['name']} ({a['size']/1e6:.1f} MB) from the official project repository?\nOnly install software you trust."): return
        self.status.set('Downloading '+a['name'])
        def work():
            path=executable(key,a['package_type']); tmp=path.with_suffix('.'+a['package_type']+'.part'); h=hashlib.sha256(); total=0
            try:
                with request(a['url']) as r, tmp.open('wb') as f:
                    if urlparse(r.geturl()).scheme!='https': raise ValueError('Insecure redirect')
                    while True:
                        chunk=r.read(256*1024)
                        if not chunk: break
                        total+=len(chunk)
                        if total>a['size']: raise ValueError('Unexpected file size')
                        f.write(chunk); h.update(chunk); self.jobs.put(('progress',100*total/max(1,a['size'])))
                digest=h.hexdigest()
                if total!=a['size']: raise ValueError('Incomplete download')
                if a['digest'] and a['digest']!='sha256:'+digest: raise ValueError('SHA-256 mismatch')
                if a['package_type']=='exe':
                    with tmp.open('rb') as f:
                        if f.read(2)!=b'MZ': raise ValueError('Not a Windows executable')
                elif not zipfile.is_zipfile(tmp):
                    raise ValueError('Not a valid JAR archive')
                else:
                    with zipfile.ZipFile(tmp) as jar:
                        if 'META-INF/MANIFEST.MF' not in jar.namelist(): raise ValueError('JAR manifest missing')
                path.parent.mkdir(parents=True,exist_ok=True); tmp.replace(path)
                self.jobs.put(('installed',(key,{'version':a['version'],'sha256':digest,'package_type':a['package_type']})))
            finally: tmp.unlink(missing_ok=True)
        self.task(work)
    def is_running(self,key):
        p=self.processes.get(key); return p is not None and p.poll() is None
    def play(self,key):
        if self.busy or self.pending_launch or self.processes: return
        try:
            package_type=self.library[key].get('package_type','exe')
            if package_type=='exe' and sys.platform!='win32': raise ValueError('Windows required')
            path=executable(key,package_type)
            if file_hash(path)!=self.library[key]['sha256']: raise ValueError('File changed. Reinstall the application.')
            command=[java_command(),'-jar',str(path)] if package_type=='jar' else [str(path)]
            self.pending_launch=True
            self.show_backdrop()
            
            self.after(250,lambda:self.spawn_game(key,command))
        except Exception as e:
            self.pending_launch=False; self.hide_backdrop(); messagebox.showerror('Launch failed',str(e))
    def spawn_game(self,key,command):
        try:
            self.processes[key]=subprocess.Popen(command,cwd=str(ROOT),shell=False)
            self.status.set('RUNNING / '+key)
        except Exception as e:
            self.hide_backdrop(); messagebox.showerror('Launch failed',str(e))
        finally: self.pending_launch=False
    def uninstall(self,key):
        if self.busy or self.pending_launch or self.is_running(key): return
        if not messagebox.askyesno('Uninstall','Remove the installed package? Game records are not removed.'): return
        try:
            executable(key,self.library[key].get('package_type','exe')).unlink(missing_ok=True); self.library.pop(key,None); save_state(self.library); self.render()
        except OSError as e: messagebox.showerror('Uninstall failed',str(e))
    def poll(self):
        global CATALOG
        for k,p in list(self.processes.items()):
            if p.poll() is not None: self.processes.pop(k,None)
        if self.backdrop is not None and not self.processes and not self.pending_launch: self.hide_backdrop()
        try:
            while True:
                kind,value=self.jobs.get_nowait()
                if kind=='manifest':
                    CATALOG,self.releases,self.errors,summary=value; self.catalog_status.set(summary); self.render()
                elif kind=='catalog_error':
                    self.catalog_status.set(value)
                elif kind=='sync_status': self.status.set(value)
                elif kind=='progress': self.progress['value']=value
                elif kind=='installed':
                    key,record=value; self.library[key]=record
                    try: save_state(self.library); self.status.set('Installed successfully')
                    except OSError as e: messagebox.showerror('Library save failed',str(e))
                    self.render()
                elif kind=='error': self.status.set('Operation failed'); messagebox.showerror('Error',value)
                elif kind=='done':
                    self.busy=False; self.progress['value']=0
                    if self.status.get().startswith('Checking'): self.status.set(self.catalog_status.get())
        except queue.Empty: pass
        self.after(100,self.poll)
    def close(self):
        if self.pending_launch or self.processes: messagebox.showinfo('Please wait','Close the running game first.'); return
        if self.busy: messagebox.showinfo('Please wait','An operation is in progress.'); return
        self.destroy()

if __name__=='__main__': Launcher().mainloop()
