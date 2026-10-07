"""
Bwoom (비움) - C드라이브 용량 분석·정리 도우미

Copyright (c) 2026 Izzy-jjc
MIT License — https://github.com/Izzy-jjc/bwoom
이 파일을 복사·수정·배포할 때는 위 저작권 표시와 LICENSE 파일을 함께 포함해야 합니다.

- 드라이브/폴더를 스캔해 폴더별 용량을 크기순으로 보여줍니다.
- 정리 추천: 지워도 되는 폴더를 [안전 / 주의 / 삭제 금지]로 알려줍니다.
- 트리맵, 큰 파일 TOP 200, 확장자별 통계 제공
- 우클릭: 탐색기에서 보기 / 경로 복사 / 휴지통으로 삭제(send2trash 설치 시)

실행:  python bwoom.py
선택 패키지:  pip install send2trash      (휴지통 삭제 기능)
exe 만들기:  pip install pyinstaller
            pyinstaller --onefile --windowed --name Bwoom --icon bwoom.ico --add-data "bwoom.ico;." --add-data "bwoom.png;." bwoom.py
"""
import os
import sys
import glob
import webbrowser
import time
import heapq
import queue
import shutil
import stat
import string
import threading
import subprocess
from collections import defaultdict

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

APP_TITLE = "Bwoom (비움)"
APP_VERSION = "1.0.0"
APP_AUTHOR = "Izzy-jjc"
APP_URL = "https://github.com/Izzy-jjc/bwoom"
TOP_N = 200                  # 큰 파일 목록 개수
MAX_CHILDREN_SHOWN = 500     # 트리에서 한 폴더당 표시할 최대 항목 수
TM_MAX_DEPTH = 3             # 트리맵 중첩 깊이
TM_MAX_RECTS = 6000          # 트리맵 최대 사각형 수
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

CATEGORIES = {
    "동영상": ({".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"}, "#e4572e"),
    "이미지": ({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".raw", ".psd", ".tif", ".tiff"}, "#f3a712"),
    "음악": ({".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}, "#7fb069"),
    "압축/이미지": ({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".img", ".vhd", ".vhdx", ".vmdk"}, "#4f8fc0"),
    "실행/시스템": ({".exe", ".dll", ".sys", ".msi", ".bin", ".dat", ".pak", ".cab", ".pf", ".tmp"}, "#8e7dbe"),
    "로그": ({".log", ".etl", ".dmp", ".trace", ".out", ".err"}, "#e07a9b"),
    "문서": ({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".hwp", ".hwpx", ".txt", ".csv"}, "#2a9d8f"),
}
OTHER = ("기타", "#9aa5b1")
EXT_TO_CAT = {ext: (name, color) for name, (exts, color) in CATEGORIES.items() for ext in exts}

# ───────────────────────── 정리 추천 규칙 ─────────────────────────
SAFE, CAUTION, NEVER = "안전", "주의", "삭제 금지"
LEVEL_ORDER = {SAFE: 0, CAUTION: 1, NEVER: 2}
LEVEL_COLOR = {SAFE: "#1b7f3b", CAUTION: "#b26a00", NEVER: "#c0392b"}


def cleanup_rules():
    """(등급, 이름, [경로 패턴], 설명, '비우기' 허용 여부, [(버튼 이름, 동작, 인자)])

    동작 종류: "pm2"(PM2 관리 창), "open"(프로그램 실행), "admin"(관리자 명령 창),
              "recycle"(휴지통 비우기)
    """
    J = os.path.join
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        env = os.environ.get
        win = env("SystemRoot", r"C:\Windows")
        sysdrv = env("SystemDrive", "C:") + "\\"
        local = env("LOCALAPPDATA", J(home, "AppData", "Local"))
        temp = env("TEMP", J(local, "Temp"))

        def chromium(base):
            return [J(local, base, "User Data", "*", "Cache"),
                    J(local, base, "User Data", "*", "Code Cache"),
                    J(local, base, "User Data", "*", "GPUCache")]

        cleanmgr = [("디스크 정리 실행", "open", ["cleanmgr"])]
        rules = [
            (SAFE, "사용자 임시 파일", [temp],
             "프로그램이 쓰고 남긴 임시 파일입니다. 사용 중인 파일은 자동으로 건너뜁니다.", True),
            (SAFE, "Windows 임시 파일", [J(win, "Temp")],
             "시스템 임시 파일입니다. 관리자 권한으로 실행해야 정리됩니다.", True),
            (SAFE, "Chrome 캐시", chromium(r"Google\Chrome"),
             "웹페이지 임시 저장본입니다. 지워도 다음 접속 때 조금 느려질 뿐입니다. 브라우저를 닫고 비우세요.", True),
            (SAFE, "Edge 캐시", chromium(r"Microsoft\Edge"),
             "웹페이지 임시 저장본입니다. 브라우저를 닫고 비우세요.", True),
            (SAFE, "네이버 웨일 캐시", chromium(r"Naver\Naver Whale"),
             "웹페이지 임시 저장본입니다. 브라우저를 닫고 비우세요.", True),
            (SAFE, "인터넷 임시 파일", [J(local, r"Microsoft\Windows\INetCache")],
             "Windows 구성 요소가 쓰는 웹 캐시입니다.", True),
            (SAFE, "오류 기록(덤프)", [J(local, "CrashDumps"), J(win, "Minidump"), J(win, "MEMORY.DMP")],
             "프로그램·시스템 오류 때 남은 기록입니다. 오류를 분석하는 중이 아니면 필요 없습니다.", True),
            (SAFE, "개발 도구 캐시", [J(local, "npm-cache"), J(local, "pip", "Cache"), J(local, "Yarn", "Cache")],
             "npm/pip/Yarn 패키지 캐시입니다. 지우면 다음 설치 때 다시 내려받습니다.", True),
            (CAUTION, "다운로드 폴더", [J(home, "Downloads")],
             "설치 파일 등이 쌓이기 쉽습니다. 필요한 파일이 있을 수 있으니 직접 확인하고 지우세요.", False),
            (CAUTION, "카카오톡 받은 파일", [J(home, "Documents", "카카오톡 받은 파일"),
                                       J(home, "OneDrive", "문서", "카카오톡 받은 파일"),
                                       J(home, "OneDrive", "Documents", "카카오톡 받은 파일")],
             "카톡으로 받은 사진·동영상·문서가 저장됩니다. 필요한 파일을 확인한 뒤 정리하세요.", False),
            (CAUTION, "휴지통", [J(sysdrv, "$Recycle.Bin")],
             "휴지통에 있는 파일입니다. 내용을 확인한 뒤 비우세요.", False,
             [("휴지통 비우기", "recycle", None)]),
            (CAUTION, "Windows 업데이트 다운로드", [J(win, "SoftwareDistribution", "Download")],
             "직접 지우지 말고 Windows '디스크 정리' → '시스템 파일 정리'로 정리하세요.", False, cleanmgr),
            (CAUTION, "이전 Windows 설치", [J(sysdrv, "Windows.old")],
             "업데이트 전 Windows 백업입니다. '디스크 정리' → '시스템 파일 정리' → '이전 Windows 설치'로 지우세요. "
             "지우면 이전 버전으로 되돌릴 수 없습니다.", False, cleanmgr),
            (CAUTION, "최대 절전 파일", [J(sysdrv, "hiberfil.sys")],
             "최대 절전 기능용 파일입니다. 끄면 이 파일이 사라지지만 최대 절전·빠른 시작 기능도 함께 꺼집니다. "
             "다시 켜려면 관리자 명령 프롬프트에서 'powercfg /h on'을 실행하세요.", False,
             [("최대 절전 끄기 (관리자)", "admin", "powercfg /h off && echo 완료되었습니다. 이 창을 닫고 다시 스캔하세요.")]),
            (NEVER, "Windows 구성 요소 저장소", [J(win, "WinSxS")],
             "직접 지우면 Windows가 손상됩니다. 아래 '구성 요소 정리' 버튼(DISM)으로만 정리하세요. "
             "하드링크 때문에 실제보다 크게 보입니다. 정리에 수십 분이 걸릴 수 있습니다.", False,
             [("구성 요소 정리 (관리자)", "admin", "Dism /Online /Cleanup-Image /StartComponentCleanup")]),
            (NEVER, "Windows Installer 캐시", [J(win, "Installer")],
             "지우면 프로그램 업데이트·제거가 안 됩니다.", False),
            (NEVER, "가상 메모리 파일", [J(sysdrv, "pagefile.sys")],
             "삭제하지 말고 '시스템 속성 → 고급 → 성능 설정 → 가상 메모리'에서 크기만 조절하세요.", False),
            (NEVER, "Windows 시스템 폴더", [J(win, "System32"), J(win, "SysWOW64")],
             "Windows 핵심 파일입니다. 절대 지우지 마세요.", False),
            (NEVER, "프로그램 설치 폴더", [J(sysdrv, "Program Files"), J(sysdrv, "Program Files (x86)")],
             "직접 지우지 말고 '설정 → 앱 → 설치된 앱'에서 제거하세요.", False,
             [("설치된 앱 열기", "open", ["explorer", "ms-settings:appsfeatures"])]),
        ]
    else:
        rules = [
        (SAFE, "사용자 캐시", [J(home, ".cache")],
         "프로그램 캐시입니다. 지우면 다시 만들어집니다.", True),
        (SAFE, "개발 도구 캐시", [J(home, ".npm", "_cacache"), J(home, ".cache", "pip")],
         "npm/pip 패키지 캐시입니다. 지우면 다음 설치 때 다시 내려받습니다.", True),
        (CAUTION, "다운로드 폴더", [J(home, "Downloads")],
         "필요한 파일이 있을 수 있으니 직접 확인하고 지우세요.", False),
        (CAUTION, "휴지통", [J(home, ".local", "share", "Trash"), J(home, ".Trash")],
         "휴지통을 비워서 정리하세요.", False),
        (NEVER, "시스템 폴더", ["/usr/bin", "/usr/lib", "/bin", "/lib", "/System"],
         "운영체제 핵심 파일입니다. 절대 지우지 마세요.", False),
    ]
    if sys.platform == "darwin":
        rules.insert(0, (SAFE, "앱 캐시", [J(home, "Library", "Caches")],
                         "앱 캐시입니다. 앱을 종료한 뒤 비우세요.", True))

    pm2_home = os.environ.get("PM2_HOME") or J(home, ".pm2")
    rules.insert(len([r for r in rules if r[0] == SAFE]), (
        CAUTION, "PM2 로그", [J(pm2_home, "logs")],
        "Node.js 앱 관리자 PM2가 남긴 로그입니다. PM2는 로그를 자동으로 자르지 않아 계속 쌓입니다. "
        "에러 로그가 크면 앱이 '에러 → 재시작'을 반복 중일 수 있습니다. "
        "[PM2 관리] 버튼에서 앱 상태와 에러 내용을 확인하고 로그를 비울 수 있습니다.", False,
        [("PM2 관리", "pm2", None)]))
    return [r if len(r) == 6 else r + ([],) for r in rules]


def expand_rule_paths(patterns):
    out = []
    for pat in patterns:
        if not pat or not os.path.isabs(pat):
            continue
        paths = glob.glob(pat) if any(ch in pat for ch in "*?") else [pat]
        for p in paths:
            if os.path.lexists(p):
                try:
                    p = os.path.realpath(p)  # 8.3 짧은 이름(예: KIMCHU~1)을 실제 이름으로
                except OSError:
                    pass
                out.append(p)
    return out


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def category_of(name):
    ext = os.path.splitext(name)[1].lower()
    return EXT_TO_CAT.get(ext, OTHER)


# ───────────────────────── 데이터 / 스캔 ─────────────────────────
class Node:
    __slots__ = ("name", "parent", "size", "files", "children", "is_dir")

    def __init__(self, name, parent, is_dir, size=0):
        self.name = name
        self.parent = parent
        self.is_dir = is_dir
        self.size = size
        self.files = 0 if is_dir else 1
        self.children = [] if is_dir else None

    @property
    def path(self):
        parts, n = [], self
        while n is not None:
            parts.append(n.name)
            n = n.parent
        return os.path.join(*reversed(parts))


def is_reparse_dir(entry):
    """정션/심볼릭 링크 폴더는 건너뜀 (중복 집계·무한루프 방지)"""
    try:
        if entry.is_symlink():
            return True
        if hasattr(entry, "is_junction") and entry.is_junction():
            return True
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True


def scan(root_path, q, stop):
    t0 = time.time()
    root = Node(os.path.abspath(root_path), None, True)
    stack = [(root, root.name)]
    order = []
    top = []
    ext = defaultdict(lambda: [0, 0])
    errors = count = 0
    last = 0.0

    while stack:
        if stop.is_set():
            break
        node, path = stack.pop()
        order.append(node)
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if is_reparse_dir(e):
                                continue
                            child = Node(e.name, node, True)
                            node.children.append(child)
                            stack.append((child, e.path))
                        else:
                            sz = e.stat(follow_symlinks=False).st_size
                            node.children.append(Node(e.name, node, False, sz))
                            node.size += sz
                            node.files += 1
                            count += 1
                            x = os.path.splitext(e.name)[1].lower() or "(확장자 없음)"
                            s = ext[x]
                            s[0] += sz
                            s[1] += 1
                            if len(top) < TOP_N:
                                heapq.heappush(top, (sz, e.path))
                            elif sz > top[0][0]:
                                heapq.heapreplace(top, (sz, e.path))
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
        now = time.monotonic()
        if now - last > 0.15:
            last = now
            q.put(("progress", count, path))

    # 하위 폴더 크기를 상위로 합산 (order는 전위순회 → 역순이면 자식이 먼저)
    for n in reversed(order):
        if n.parent is not None:
            n.parent.size += n.size
            n.parent.files += n.files
    for n in order:
        n.children.sort(key=lambda c: c.size, reverse=True)

    q.put(("done", root, sorted(top, reverse=True), dict(ext), errors,
           time.time() - t0, stop.is_set()))


# ───────────────────────── 트리맵 (squarified) ─────────────────────────
def squarify(items, x, y, w, h):
    """items: [(size, obj)] 크기 내림차순. 반환: [(x, y, w, h, obj)]"""
    rects = []
    total = sum(s for s, _ in items)
    if total <= 0 or w <= 0 or h <= 0:
        return rects
    scale = w * h / total
    items = [(s * scale, o) for s, o in items]

    def worst(row, side):
        s = sum(a for a, _ in row)
        mx = max(a for a, _ in row)
        mn = min(a for a, _ in row)
        if s == 0 or mn == 0:
            return float("inf")
        return max(side * side * mx / (s * s), s * s / (side * side * mn))

    def layout(row, x, y, w, h):
        s = sum(a for a, _ in row)
        if w >= h:
            cw = s / h if h else 0
            cy = y
            for a, o in row:
                ch = a / cw if cw else 0
                rects.append((x, cy, cw, ch, o))
                cy += ch
            return x + cw, y, w - cw, h
        rh = s / w if w else 0
        cx = x
        for a, o in row:
            rw = a / rh if rh else 0
            rects.append((cx, y, rw, rh, o))
            cx += rw
        return x, y + rh, w, h - rh

    row, i = [], 0
    while i < len(items):
        side = min(w, h)
        item = items[i]
        if not row or worst(row + [item], side) <= worst(row, side):
            row.append(item)
            i += 1
        else:
            x, y, w, h = layout(row, x, y, w, h)
            row = []
    if row:
        layout(row, x, y, w, h)
    return rects


def shade(hex_color, factor):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    f = lambda v: max(0, min(255, int(v * factor)))
    return f"#{f(r):02x}{f(g):02x}{f(b):02x}"


# ───────────────────────── GUI ─────────────────────────
def resource_path(name):
    """exe(PyInstaller)로 묶였을 때와 .py로 실행할 때 모두 동작하는 파일 경로"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def apply_icon(win):
    """창 제목줄·작업 표시줄 아이콘 (bwoom.ico / bwoom.png가 없으면 기본 아이콘 유지)"""
    try:
        ico = resource_path("bwoom.ico")
        if sys.platform == "win32" and os.path.exists(ico):
            win.iconbitmap(default=ico)
            return
        png = resource_path("bwoom.png")
        if os.path.exists(png):
            img = tk.PhotoImage(file=png)
            win.iconphoto(True, img)
            win._icon_ref = img  # 이미지가 지워지지 않게 붙잡아 둠
    except Exception:
        pass

UI_FONT = "맑은 고딕" if sys.platform == "win32" else "Noto Sans CJK KR"


class TabBar(tk.Frame):
    """선택된 탭이 확실히 구분되는 탭 줄 (Windows 기본 탭은 색을 바꿀 수 없어 직접 그림)"""
    SEL_BG, SEL_FG = "#1f6feb", "#ffffff"
    OFF_BG, OFF_FG, HOVER_BG = "#e4e7eb", "#3a3f45", "#d0d5db"
    HOT_BG, HOT_FG = "#fff1dc", "#b45309"   # 정리 추천 탭 (선택 안 됐을 때도 눈에 띄게)

    def __init__(self, master, nb):
        super().__init__(master)
        self.nb = nb
        self.accent = None
        self.row = tk.Frame(self)
        self.row.pack(fill="x")
        tk.Frame(self, height=3, bg=self.SEL_BG).pack(fill="x")  # 선택 탭과 이어지는 밑줄
        nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh(), add="+")

    def refresh(self):
        for w in self.row.winfo_children():
            w.destroy()
        cur = self.nb.select()
        for tab in self.nb.tabs():
            sel = tab == cur
            hot = tab == self.accent
            bg = self.SEL_BG if sel else (self.HOT_BG if hot else self.OFF_BG)
            fg = self.SEL_FG if sel else (self.HOT_FG if hot else self.OFF_FG)
            lb = tk.Label(self.row, text=self.nb.tab(tab, "text"), bg=bg, fg=fg, padx=16, pady=6,
                          font=(UI_FONT, 10, "bold" if sel or hot else "normal"), cursor="hand2")
            lb.pack(side="left", padx=(0, 2))
            lb.bind("<Button-1>", lambda e, t=tab: self.nb.select(t))
            if not sel:
                lb.bind("<Enter>", lambda e, w=lb: w.config(bg=self.HOVER_BG))
                lb.bind("<Leave>", lambda e, w=lb, b=bg: w.config(bg=b))
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE + self._admin_suffix())
        apply_icon(self)
        self.geometry("1250x820")
        self.minsize(900, 600)

        self.q = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.root_node = None
        self.top = []
        self.iid2node = {}
        self.node2iid = {}
        self.populated = set()
        self.tm_node = None
        self.tm_items = {}
        self._tm_job = None
        self._tm_count = 0
        self._suppress_tm = False
        self._menu_node = None
        self.recs = []
        self.rec_iid = {}
        self.protected = [(_norm(p), name, desc)
                          for level, name, pats, desc, *_ in cleanup_rules() if level == NEVER
                          for p in expand_rule_paths(pats)]

        self._build_ui()
        self._load_drives()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ---------- 초기화 ----------
    @staticmethod
    def _admin_suffix():
        if sys.platform != "win32":
            return ""
        try:
            import ctypes
            return "" if ctypes.windll.shell32.IsUserAnAdmin() else "  (관리자 권한 아님 - 일부 폴더는 집계되지 않음)"
        except Exception:
            return ""

    def _build_ui(self):
        style = ttk.Style(self)
        if sys.platform == "win32":
            style.theme_use("vista")
            default_font = ("맑은 고딕", 9)
            self.option_add("*Font", default_font)
        style.configure("Treeview", rowheight=22)
        style.layout("Bare.TNotebook.Tab", [])  # 기본 탭은 숨기고 TabBar로 직접 그림
        style.configure("Bare.TNotebook", borderwidth=0)

        # 상단 바
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="드라이브/폴더:").pack(side="left")
        self.target = ttk.Combobox(bar, width=40)
        self.target.pack(side="left", padx=4)
        self.target.bind("<<ComboboxSelected>>", self._update_usage)
        self.target.bind("<Return>", lambda e: self.start_scan())
        ttk.Button(bar, text="찾아보기…", command=self._browse).pack(side="left", padx=2)
        self.btn_scan = ttk.Button(bar, text="스캔", command=self.start_scan)
        self.btn_scan.pack(side="left", padx=2)
        self.btn_stop = ttk.Button(bar, text="중지", command=self.stop.set, state="disabled")
        self.btn_stop.pack(side="left", padx=2)
        ttk.Button(bar, text="ⓘ 정보", command=lambda: AboutWindow(self)).pack(side="right", padx=2)

        ubar = ttk.Frame(self, padding=(8, 0, 8, 4))
        ubar.pack(fill="x")
        ttk.Label(ubar, text="디스크 사용량:").pack(side="left")
        self.usage_bar = ttk.Progressbar(ubar, length=260, maximum=100)
        self.usage_bar.pack(side="left", padx=6)
        self.usage_lbl = ttk.Label(ubar, text="")
        self.usage_lbl.pack(side="left")

        # 본문: 위(탭) / 아래(트리맵)
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8)

        tabwrap = ttk.Frame(paned)
        paned.add(tabwrap, weight=3)
        self.nb = ttk.Notebook(tabwrap, style="Bare.TNotebook")
        self.tabbar = TabBar(tabwrap, self.nb)
        self.tabbar.pack(fill="x")
        self.nb.pack(fill="both", expand=True)

        # 탭1: 폴더 트리
        f1 = ttk.Frame(self.nb)
        self.tree = ttk.Treeview(f1, columns=("size", "pct", "files"))
        self.tree.heading("#0", text="이름")
        self.tree.heading("size", text="크기")
        self.tree.heading("pct", text="상위 대비 %")
        self.tree.heading("files", text="파일 수")
        self.tree.column("#0", width=520)
        self.tree.column("size", width=110, anchor="e")
        self.tree.column("pct", width=100, anchor="e")
        self.tree.column("files", width=100, anchor="e")
        self.tree.tag_configure("dir", foreground="#1f3b73")
        self.tree.tag_configure("more", foreground="#888888")
        self._with_scroll(f1, self.tree)
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._populate(self.tree.focus()))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-3>", self._on_tree_right)
        self.nb.add(f1, text="폴더")

        # 탭: 정리 추천
        f4 = ttk.Frame(self.nb)
        head4 = ttk.Frame(f4, padding=(4, 6))
        head4.pack(fill="x")
        self.rec_summary = ttk.Label(head4, text="스캔하면 지워도 되는 공간을 찾아 드립니다.",
                                     font=("맑은 고딕", 10, "bold"))
        self.rec_summary.pack(side="left")
        self.btn_rec_empty = ttk.Button(head4, text="비우기 (바로 삭제)", command=self._rec_empty, state="disabled")
        self.btn_rec_empty.pack(side="right", padx=2)
        self.btn_rec_open = ttk.Button(head4, text="폴더 열기", command=self._rec_open, state="disabled")
        self.btn_rec_open.pack(side="right", padx=2)
        self.rec_actions = ttk.Frame(head4)  # 항목별 정리 버튼 (PM2 관리, 최대 절전 끄기 등)
        self.rec_actions.pack(side="right", padx=6)
        self.rec_desc = ttk.Label(f4, text="항목을 선택하면 설명이 나옵니다.", wraplength=1150,
                                  justify="left", padding=(6, 6))
        self.rec_desc.pack(side="bottom", fill="x")
        body4 = ttk.Frame(f4)
        body4.pack(fill="both", expand=True)
        self.reclist = ttk.Treeview(body4, columns=("level", "name", "size", "path"), show="headings")
        for col, txt, w, anc in (("level", "등급", 80, "center"), ("name", "항목", 200, "w"),
                                 ("size", "크기", 110, "e"), ("path", "경로", 700, "w")):
            self.reclist.heading(col, text=txt)
            self.reclist.column(col, width=w, anchor=anc)
        for lv, color in LEVEL_COLOR.items():
            self.reclist.tag_configure(lv, foreground=color)
        self._with_scroll(body4, self.reclist)
        self.reclist.bind("<<TreeviewSelect>>", self._on_rec_select)
        self.reclist.bind("<Double-1>", self._on_rec_dbl)
        self.nb.add(f4, text="★ 정리 추천")
        self.rec_tab = f4

        # 탭2: 큰 파일
        f2 = ttk.Frame(self.nb)
        self.toplist = ttk.Treeview(f2, columns=("size", "path"), show="headings")
        self.toplist.heading("size", text="크기")
        self.toplist.heading("path", text="경로")
        self.toplist.column("size", width=110, anchor="e", stretch=False)
        self.toplist.column("path", width=800)
        self._with_scroll(f2, self.toplist)
        self.toplist.bind("<Double-1>", self._on_top_dbl)
        self.toplist.bind("<Button-3>", self._on_top_right)
        self.nb.add(f2, text=f"큰 파일 TOP {TOP_N}")

        # 탭3: 확장자별
        f3 = ttk.Frame(self.nb)
        self.extlist = ttk.Treeview(f3, columns=("ext", "cat", "size", "count", "pct"), show="headings")
        for col, txt, w, anc in (("ext", "확장자", 140, "w"), ("cat", "분류", 110, "w"),
                                 ("size", "크기", 110, "e"), ("count", "파일 수", 100, "e"),
                                 ("pct", "비율", 80, "e")):
            self.extlist.heading(col, text=txt)
            self.extlist.column(col, width=w, anchor=anc)
        self._with_scroll(f3, self.extlist)
        self.nb.add(f3, text="확장자별")
        self.tabbar.accent = str(self.rec_tab)
        self.tabbar.refresh()

        # 트리맵
        tmf = ttk.Frame(paned)
        paned.add(tmf, weight=2)
        head = ttk.Frame(tmf)
        head.pack(fill="x", pady=(4, 2))
        ttk.Button(head, text="▲ 상위 폴더", command=self._tm_up).pack(side="left")
        self.tm_title = ttk.Label(head, text="트리맵: 스캔 후 표시됩니다 (더블클릭: 폴더로 들어가기)")
        self.tm_title.pack(side="left", padx=8)
        legend = ttk.Frame(head)
        legend.pack(side="right")
        for name, (_, color) in list(CATEGORIES.items()) + [(OTHER[0], (None, OTHER[1]))]:
            tk.Label(legend, text="  ", bg=color).pack(side="left", padx=(6, 2))
            ttk.Label(legend, text=name).pack(side="left")
        self.canvas = tk.Canvas(tmf, bg="#20242b", highlightthickness=0, height=260)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._schedule_tm())
        self.canvas.bind("<Motion>", self._on_tm_motion)
        self.canvas.bind("<Button-1>", self._on_tm_click)
        self.canvas.bind("<Double-1>", self._on_tm_dbl)
        self.canvas.bind("<Button-3>", self._on_tm_right)

        # 상태바
        self.status = ttk.Label(self, text="드라이브를 선택하고 [스캔]을 누르세요.", anchor="w", padding=(8, 3))
        self.status.pack(fill="x")

        # 우클릭 메뉴
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="탐색기에서 보기", command=lambda: self.open_location(self._menu_node))
        self.menu.add_command(label="경로 복사", command=lambda: self.copy_path(self._menu_node))
        self.menu.add_separator()
        self.menu.add_command(label="휴지통으로 삭제", command=lambda: self.delete_node(self._menu_node))

    @staticmethod
    def _with_scroll(parent, widget):
        ys = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)
        widget.configure(yscrollcommand=ys.set)
        widget.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")

    def _load_drives(self):
        if sys.platform == "win32":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        else:
            drives = ["/", os.path.expanduser("~")]
        self.target["values"] = drives
        if drives:
            self.target.set(drives[0])
        self._update_usage()

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.target.set(os.path.normpath(d))
            self._update_usage()

    def _update_usage(self, *_):
        p = self.target.get().strip()
        try:
            u = shutil.disk_usage(p)
        except OSError:
            self.usage_lbl.config(text="")
            self.usage_bar["value"] = 0
            return
        pct = u.used / u.total * 100 if u.total else 0
        self.usage_lbl.config(
            text=f"전체 {fmt_size(u.total)}  |  사용 {fmt_size(u.used)} ({pct:.1f}%)  |  여유 {fmt_size(u.free)}  ")
        self.usage_bar["value"] = pct

    # ---------- 스캔 ----------
    def start_scan(self):
        path = self.target.get().strip()
        if not os.path.isdir(path):
            messagebox.showerror(APP_TITLE, f"폴더를 찾을 수 없습니다:\n{path}")
            return
        if self.worker and self.worker.is_alive():
            return
        self._clear()
        self._update_usage()
        self.stop.clear()
        self.btn_scan.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status.config(text="스캔 시작…")
        self.worker = threading.Thread(target=scan, args=(path, self.q, self.stop), daemon=True)
        self.worker.start()

    def _clear(self):
        self.tree.delete(*self.tree.get_children())
        self.toplist.delete(*self.toplist.get_children())
        self.extlist.delete(*self.extlist.get_children())
        self.reclist.delete(*self.reclist.get_children())
        self.recs = []
        self.rec_iid.clear()
        self.nb.tab(self.rec_tab, text="★ 정리 추천")
        self.tabbar.refresh()
        self.canvas.delete("all")
        self.iid2node.clear()
        self.node2iid.clear()
        self.populated.clear()
        self.tm_items.clear()
        self.root_node = None
        self.tm_node = None
        self.top = []

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "progress":
                    self.status.config(text=f"스캔 중…  파일 {msg[1]:,}개  |  {msg[2]}")
                elif msg[0] == "done":
                    self._on_done(*msg[1:])
                elif msg[0] == "call":  # 작업 스레드가 끝난 뒤 화면 갱신 (메인 스레드에서 실행)
                    msg[1]()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_done(self, root, top, ext, errors, elapsed, stopped):
        self.btn_scan.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.root_node = root
        self.top = top

        iid = self._insert("", root)
        self._populate(iid)
        self.tree.item(iid, open=True)
        self._fill_top()

        total = root.size or 1
        for x, (sz, cnt) in sorted(ext.items(), key=lambda kv: kv[1][0], reverse=True):
            cat = category_of("a" + x)[0] if x.startswith(".") else OTHER[0]
            self.extlist.insert("", "end", values=(x, cat, fmt_size(sz), f"{cnt:,}", f"{sz / total * 100:.1f} %"))

        self.draw_treemap(root)
        self._build_recs()
        if self.recs:
            self.nb.select(self.rec_tab)  # 스캔이 끝나면 정리 추천을 먼저 보여줌
        head = "중지됨 (부분 결과)" if stopped else "완료"
        safe = sum(r["node"].size for r in self.recs if r["level"] == SAFE)
        self.status.config(text=f"{head}:  {fmt_size(root.size)}  |  파일 {root.files:,}개  |  "
                                f"{elapsed:.1f}초  |  접근 불가 {errors:,}건  |  "
                                f"바로 정리 가능 {fmt_size(safe)} → [정리 추천] 탭")

    # ---------- 폴더 트리 ----------
    def _values(self, node):
        base = node.parent.size if node.parent else node.size
        pct = f"{node.size / base * 100:.1f} %" if base else "-"
        return fmt_size(node.size), pct, (f"{node.files:,}" if node.is_dir else "")

    def _insert(self, parent_iid, node):
        iid = self.tree.insert(parent_iid, "end", text=node.name, values=self._values(node),
                               tags=("dir",) if node.is_dir else ())
        self.iid2node[iid] = node
        self.node2iid[node] = iid
        if node.is_dir and node.children:
            self.tree.insert(iid, "end", text="…")  # 펼치기용 자리표시
        return iid

    def _populate(self, iid):
        node = self.iid2node.get(iid)
        if node is None or not node.is_dir or node in self.populated:
            return
        self.populated.add(node)
        self.tree.delete(*self.tree.get_children(iid))
        for c in node.children[:MAX_CHILDREN_SHOWN]:
            self._insert(iid, c)
        rest = node.children[MAX_CHILDREN_SHOWN:]
        if rest:
            self.tree.insert(iid, "end", text=f"… 외 {len(rest):,}개 항목",
                             values=(fmt_size(sum(c.size for c in rest)), "", ""), tags=("more",))

    def _on_tree_select(self, _):
        if self._suppress_tm:
            return
        sel = self.tree.selection()
        node = self.iid2node.get(sel[0]) if sel else None
        if node:
            self.draw_treemap(node if node.is_dir else node.parent)

    def reveal(self, node):
        chain, n = [], node
        while n is not None:
            chain.append(n)
            n = n.parent
        for anc in reversed(chain[1:]):
            iid = self.node2iid.get(anc)
            if iid is None:
                return
            self._populate(iid)
            self.tree.item(iid, open=True)
        iid = self.node2iid.get(node)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def find_node(self, path):
        root = self.root_node
        if root is None:
            return None
        try:
            rel = os.path.relpath(path, root.name)
        except ValueError:  # 다른 드라이브
            return None
        node = root
        if rel == ".":
            return node
        if rel.startswith(".."):
            return None
        fold = (lambda s: s.lower()) if sys.platform == "win32" else (lambda s: s)
        for part in rel.split(os.sep):
            key = fold(part)
            node = next((c for c in (node.children or []) if fold(c.name) == key), None)
            if node is None:
                return None
        return node

    # ---------- 정리 추천 ----------
    def _build_recs(self):
        self.recs = []
        seen = set()
        for level, name, pats, desc, can_empty, actions in cleanup_rules():
            for p in expand_rule_paths(pats):
                node = self.find_node(p)
                if node is None or id(node) in seen or node.size == 0:
                    continue
                seen.add(id(node))
                self.recs.append({"level": level, "name": name, "node": node, "desc": desc,
                                  "can_empty": can_empty and node.is_dir, "actions": actions})

        def inside_other(r):  # 다른 추천 항목 안에 들어 있으면 중복 집계 방지
            n = r["node"].parent
            while n is not None:
                if id(n) in seen:
                    return True
                n = n.parent
            return False

        self.recs = [r for r in self.recs if not inside_other(r)]
        self._fill_recs()

    def _attached(self, node):
        while node.parent is not None:
            if node not in node.parent.children:
                return False
            node = node.parent
        return node is self.root_node

    def _fill_recs(self):
        self.recs = [r for r in self.recs if r["node"].size > 0 and self._attached(r["node"])]
        self.recs.sort(key=lambda r: (LEVEL_ORDER[r["level"]], -r["node"].size))
        self.reclist.delete(*self.reclist.get_children())
        self.rec_iid.clear()
        for r in self.recs:
            iid = self.reclist.insert("", "end", tags=(r["level"],),
                                      values=(r["level"], r["name"], fmt_size(r["node"].size), r["node"].path))
            self.rec_iid[iid] = r
        safe = sum(r["node"].size for r in self.recs if r["level"] == SAFE)
        caution = sum(r["node"].size for r in self.recs if r["level"] == CAUTION)
        self.nb.tab(self.rec_tab, text=f"★ 정리 추천 ({fmt_size(safe + caution)})" if self.recs else "★ 정리 추천")
        self.tabbar.refresh()
        if self.recs:
            self.rec_summary.config(text=f"바로 정리 가능: {fmt_size(safe)}     확인 후 정리 가능: {fmt_size(caution)}")
        else:
            self.rec_summary.config(text="이 스캔 범위에서는 추천할 정리 항목을 찾지 못했습니다. "
                                         "(시스템 드라이브 전체를 스캔해 보세요)")
        self.btn_rec_open.config(state="disabled")
        self.btn_rec_empty.config(state="disabled")
        self._set_rec_actions(None)
        self.rec_desc.config(text="항목을 선택하면 설명이 나옵니다.")

    def _set_rec_actions(self, r):
        for w in self.rec_actions.winfo_children():
            w.destroy()
        if not r:
            return
        for label, kind, arg in r["actions"]:
            if kind in ("admin", "recycle") and sys.platform != "win32":
                continue
            ttk.Button(self.rec_actions, text=label,
                       command=lambda k=kind, a=arg, rr=r: self.run_action(k, a, rr)).pack(side="left", padx=2)

    def run_action(self, kind, arg, r):
        if kind == "pm2":
            Pm2Window(self, r["node"])
        elif kind == "open":
            try:
                subprocess.Popen(arg)
                self.status.config(text="정리가 끝나면 [스캔]을 다시 눌러 결과를 확인하세요.")
            except OSError as ex:
                messagebox.showerror(APP_TITLE, f"실행하지 못했습니다:\n{ex}")
        elif kind == "admin":
            if not messagebox.askyesno(
                    "관리자 권한으로 실행",
                    f"다음 명령을 관리자 권한으로 실행합니다.\n\n{arg.split(' && ')[0]}\n\n"
                    f"{r['desc']}\n\n계속할까요? (Windows 권한 확인 창이 뜹니다)"):
                return
            try:
                import ctypes
                rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/k {arg}", None, 1)
                if rc <= 32:
                    raise OSError(f"오류 코드 {rc} (권한 확인 창에서 취소했을 수 있습니다)")
                self.status.config(text="명령 창에서 작업이 끝나면 [스캔]을 다시 눌러 결과를 확인하세요.")
            except Exception as ex:
                messagebox.showerror(APP_TITLE, f"실행하지 못했습니다:\n{ex}")
        elif kind == "recycle":
            try:
                import ctypes
                ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0)  # Windows 확인 창이 뜸
                self.status.config(text="휴지통을 비웠다면 [스캔]을 다시 눌러 결과를 확인하세요.")
            except Exception as ex:
                messagebox.showerror(APP_TITLE, f"휴지통을 비우지 못했습니다:\n{ex}")

    def resync_files(self, node):
        """폴더 안 파일들의 현재 크기를 다시 읽어 반영 (PM2 로그 비우기 등 외부 작업 후)"""
        if node is None or not node.is_dir or not self._attached(node):
            return
        gone = []
        for c in node.children:
            if c.is_dir:
                continue
            try:
                new = os.stat(c.path).st_size
            except OSError:
                gone.append(c)
                continue
            delta = new - c.size
            if delta:
                c.size = new
                a = node
                while a is not None:
                    a.size += delta
                    a = a.parent
        node.children.sort(key=lambda c: c.size, reverse=True)
        sizes = {c.path: c.size for c in node.children if not c.is_dir}
        self.top = sorted(((sizes.get(p, s), p) for s, p in self.top), reverse=True)
        if gone:
            self._remove_nodes(gone)
        a = node
        while a is not None:
            self._set_values(a)
            a = a.parent
        for c in node.children:
            self._set_values(c)
        self._fill_top()
        self.draw_treemap(self.tm_node)
        self._fill_recs()
        self._update_usage()

    def _rec_selected(self):
        sel = self.reclist.selection()
        return self.rec_iid.get(sel[0]) if sel else None

    def _on_rec_select(self, _):
        r = self._rec_selected()
        if not r:
            return
        self.rec_desc.config(text=f"[{r['level']}] {r['name']}  —  {r['desc']}")
        self.btn_rec_open.config(state="normal")
        ok = r["can_empty"] and bool(r["node"].children)
        self.btn_rec_empty.config(state="normal" if ok else "disabled")
        self._set_rec_actions(r)

    def _on_rec_dbl(self, _):
        r = self._rec_selected()
        if r:
            self.nb.select(0)
            self.reveal(r["node"])

    def _rec_open(self):
        r = self._rec_selected()
        if r:
            self.open_location(r["node"])

    def _rec_empty(self):
        """'안전' 항목 비우기: 캐시·임시 파일은 휴지통에 둘 이유가 없으므로 바로 삭제"""
        r = self._rec_selected()
        if not r or not r["can_empty"]:
            return
        node = r["node"]
        kids = list(node.children)
        if not kids:
            return
        if not messagebox.askyesno(
                "비우기 확인",
                f"'{r['name']}' 안의 항목 {len(kids):,}개 ({fmt_size(node.size)})를 삭제할까요?\n\n"
                f"{node.path}\n\n"
                f"캐시·임시 파일이라 휴지통을 거치지 않고 바로 삭제합니다.\n"
                f"사용 중인 파일은 자동으로 건너뜁니다."):
            return
        self.btn_rec_empty.config(state="disabled")
        self.status.config(text=f"정리 중… {node.path}")
        root_path = node.path
        targets = [(k.path, k.is_dir) for k in kids]
        before = node.size

        def work():
            failed = [0]

            def force(func, path, _):  # 읽기 전용 파일은 속성을 풀고 한 번 더 시도
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except OSError:
                    failed[0] += 1

            kw = {"onexc": force} if sys.version_info >= (3, 12) else {"onerror": force}
            for p, is_dir in targets:
                try:
                    if is_dir:
                        shutil.rmtree(p, **kw)
                    else:
                        try:
                            os.remove(p)
                        except PermissionError:
                            force(os.remove, p, None)
                except OSError:
                    failed[0] += 1
            # 실제로 남은 상태를 다시 스캔 (일부만 지워진 폴더도 정확히 반영)
            q2 = queue.Queue()
            scan(root_path, q2, threading.Event())
            fresh, fresh_top = None, []
            while not q2.empty():
                m = q2.get()
                if m[0] == "done":
                    fresh, fresh_top = m[1], m[2]
            self.q.put(("call", lambda: self._on_emptied(r, before, fresh, fresh_top, failed[0])))

        threading.Thread(target=work, daemon=True).start()

    def _on_emptied(self, r, before, fresh, fresh_top, failed):
        node = r["node"]
        if fresh is not None and self._attached(node):
            self.replace_subtree(node, fresh, fresh_top)
        freed = max(0, before - node.size)
        msg = f"'{r['name']}' 정리 완료: {fmt_size(freed)} 확보"
        if failed:
            msg += f"  (사용 중이거나 권한이 없어 건너뜀 {failed:,}개)"
        self.status.config(text=msg)

    def replace_subtree(self, node, fresh, fresh_top):
        """폴더를 다시 스캔한 결과(fresh)로 기존 데이터·화면을 교체"""
        dsize, dfiles = fresh.size - node.size, fresh.files - node.files
        a = node.parent
        while a is not None:
            a.size += dsize
            a.files += dfiles
            a = a.parent
        node.size, node.files = fresh.size, fresh.files
        stack = list(node.children)
        while stack:  # 예전 하위 항목의 화면 연결 정리
            c = stack.pop()
            iid = self.node2iid.pop(c, None)
            if iid:
                self.iid2node.pop(iid, None)
            self.populated.discard(c)
            if c.children:
                stack.extend(c.children)
        node.children = fresh.children
        for c in node.children:
            c.parent = node
        iid = self.node2iid.get(node)
        if iid and self.tree.exists(iid):
            was_open = self.tree.item(iid, "open")
            self.tree.delete(*self.tree.get_children(iid))
            self.populated.discard(node)
            if node.children:
                if was_open:
                    self._populate(iid)
                else:
                    self.tree.insert(iid, "end", text="…")
        a = node
        while a is not None:
            self._set_values(a)
            a = a.parent
        if node.parent:
            for sib in node.parent.children:
                self._set_values(sib)
        p = node.path
        self.top = [t for t in self.top if not (t[1] == p or t[1].startswith(p + os.sep))] + list(fresh_top)
        self.top = sorted(self.top, reverse=True)[:TOP_N]
        self._fill_top()
        target = self.tm_node
        n = target
        while n is not None and n is not node:
            n = n.parent
        if n is node and target is not node and not self._attached(target):
            target = node
        self.draw_treemap(target)
        self._fill_recs()
        self._update_usage()

    def protected_reason(self, node):
        p = _norm(node.path)
        for rp, name, desc in self.protected:
            if p == rp or p.startswith(rp + os.sep) or rp.startswith(p + os.sep):
                return name, desc
        return None

    def _on_tree_right(self, e):
        iid = self.tree.identify_row(e.y)
        node = self.iid2node.get(iid)
        if node:
            self.tree.selection_set(iid)
            self._popup(node, e)

    # ---------- 큰 파일 ----------
    def _fill_top(self):
        self.toplist.delete(*self.toplist.get_children())
        for sz, p in self.top:
            self.toplist.insert("", "end", values=(fmt_size(sz), p))

    def _top_node(self, e):
        iid = self.toplist.identify_row(e.y)
        if not iid:
            return None
        return self.find_node(self.toplist.item(iid, "values")[1])

    def _on_top_dbl(self, e):
        node = self._top_node(e)
        if node:
            self.nb.select(0)
            self.reveal(node)

    def _on_top_right(self, e):
        node = self._top_node(e)
        if node:
            self.toplist.selection_set(self.toplist.identify_row(e.y))
            self._popup(node, e)

    # ---------- 트리맵 ----------
    def _schedule_tm(self):
        if self._tm_job:
            self.after_cancel(self._tm_job)
        self._tm_job = self.after(120, lambda: self.draw_treemap(self.tm_node))

    def draw_treemap(self, node):
        self.tm_node = node
        c = self.canvas
        c.delete("all")
        self.tm_items.clear()
        self._tm_count = 0
        W, H = c.winfo_width(), c.winfo_height()
        if node is None or W < 10 or H < 10:
            return
        self.tm_title.config(text=f"트리맵: {node.path}   ({fmt_size(node.size)})")
        self._tm(node, 0, 0, W, H, 0)

    def _tm(self, node, x, y, w, h, depth):
        c = self.canvas
        items = [(ch.size, ch) for ch in node.children[:2000] if ch.size > 0]
        for rx, ry, rw, rh, ch in squarify(items, x, y, w, h):
            if self._tm_count > TM_MAX_RECTS:
                return
            if rw < 1 or rh < 1:
                continue
            self._tm_count += 1
            x2, y2 = rx + rw, ry + rh
            if ch.is_dir:
                rid = c.create_rectangle(rx, ry, x2, y2, fill="#3b4250", outline="#15181d")
                self.tm_items[rid] = ch
                if depth < TM_MAX_DEPTH and rw > 30 and rh > 30:
                    if rw > 50:
                        tid = c.create_text(rx + 4, ry + 2, anchor="nw", fill="#e8ecf1",
                                            text=self._fit(ch.name, rw), font=("맑은 고딕", 8, "bold"))
                        self.tm_items[tid] = ch
                    self._tm(ch, rx + 2, ry + 16, rw - 4, rh - 18, depth + 1)
            else:
                color = category_of(ch.name)[1]
                rid = c.create_rectangle(rx, ry, x2, y2, fill=color, outline=shade(color, 0.6))
                self.tm_items[rid] = ch
                if rw > 60 and rh > 16:
                    tid = c.create_text(rx + 3, ry + 2, anchor="nw", fill="#111111",
                                        text=self._fit(ch.name, rw), font=("맑은 고딕", 8))
                    self.tm_items[tid] = ch

    @staticmethod
    def _fit(text, width):
        maxc = max(1, int(width / 7))
        return text if len(text) <= maxc else text[:maxc - 1] + "…"

    def _tm_hit(self, e):
        ids = self.canvas.find_overlapping(e.x, e.y, e.x, e.y)
        for i in reversed(ids):
            if i in self.tm_items:
                return self.tm_items[i]
        return None

    def _on_tm_motion(self, e):
        node = self._tm_hit(e)
        if node:
            self.status.config(text=f"{node.path}   —   {fmt_size(node.size)}")

    def _on_tm_click(self, e):
        node = self._tm_hit(e)
        if node:
            self.nb.select(0)
            self._suppress_tm = True
            try:
                self.reveal(node)
                self.update_idletasks()
            finally:
                self.after(50, lambda: setattr(self, "_suppress_tm", False))

    def _on_tm_dbl(self, e):
        node = self._tm_hit(e)
        if node:
            target = node if node.is_dir else node.parent
            self.draw_treemap(target)

    def _on_tm_right(self, e):
        node = self._tm_hit(e)
        if node:
            self._popup(node, e)

    def _tm_up(self):
        if self.tm_node is not None and self.tm_node.parent is not None:
            self.draw_treemap(self.tm_node.parent)

    # ---------- 동작 ----------
    def _popup(self, node, e):
        self._menu_node = node
        self.menu.entryconfig("휴지통으로 삭제", state="normal" if node.parent is not None else "disabled")
        self.menu.tk_popup(e.x_root, e.y_root)

    def open_location(self, node):
        if node is None:
            return
        p = node.path
        try:
            if sys.platform == "win32":
                subprocess.Popen(f'explorer /select,"{p}"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", p])
            else:
                subprocess.Popen(["xdg-open", p if node.is_dir else os.path.dirname(p)])
        except OSError as ex:
            messagebox.showerror(APP_TITLE, str(ex))

    def copy_path(self, node):
        if node:
            self.clipboard_clear()
            self.clipboard_append(node.path)
            self.status.config(text=f"복사됨: {node.path}")

    def delete_node(self, node):
        if node is None or node.parent is None:
            return
        reason = self.protected_reason(node)
        if reason:
            messagebox.showwarning("삭제할 수 없음",
                                   f"이 항목은 '{reason[0]}'에 해당해 삭제를 막았습니다.\n\n{reason[1]}")
            return
        if send2trash is None:
            messagebox.showinfo(APP_TITLE, "휴지통 삭제 기능을 쓰려면 먼저 설치하세요:\n\npip install send2trash")
            return
        p = node.path
        if not messagebox.askyesno("삭제 확인", f"휴지통으로 이동할까요?\n\n{p}\n크기: {fmt_size(node.size)}"):
            return
        try:
            send2trash(p)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, f"삭제 실패:\n{ex}")
            return
        self._remove_nodes([node])
        self.status.config(text=f"휴지통으로 이동함: {p}  ({fmt_size(node.size)})")
        self._update_usage()

    def _set_values(self, node):
        iid = self.node2iid.get(node)
        if iid and self.tree.exists(iid):
            self.tree.item(iid, values=self._values(node))

    def _remove_nodes(self, nodes):
        """삭제된 항목들을 데이터와 화면에서 한꺼번에 제거"""
        if not nodes:
            return
        removed = {id(n) for n in nodes}
        paths = {n.path for n in nodes}
        parents = {}
        for n in nodes:
            a = n.parent
            while a is not None:
                a.size -= n.size
                a.files -= n.files
                a = a.parent
            parents[id(n.parent)] = n.parent
            iid = self.node2iid.pop(n, None)
            if iid:
                self.iid2node.pop(iid, None)
                if self.tree.exists(iid):
                    self.tree.delete(iid)
        for p in parents.values():
            p.children = [c for c in p.children if id(c) not in removed]
            a = p
            while a is not None:
                self._set_values(a)
                a = a.parent
            for sib in p.children:
                self._set_values(sib)

        def gone(path):
            while True:
                if path in paths:
                    return True
                up = os.path.dirname(path)
                if up == path:
                    return False
                path = up

        self.top = [t for t in self.top if not gone(t[1])]
        self._fill_top()
        target, n = self.tm_node, self.tm_node
        while n is not None:
            if id(n) in removed:
                target = n.parent
            n = n.parent
        self.draw_treemap(target)
        self._fill_recs()

    def _on_close(self):
        self.stop.set()
        self.destroy()


# ───────────────────────── 정보 창 ─────────────────────────
class AboutWindow(tk.Toplevel):
    """프로그램 정보: 버전, 제작자, GitHub 주소, 라이선스"""

    def __init__(self, app):
        super().__init__(app)
        self.title("Bwoom 정보")
        self.resizable(False, False)
        self.transient(app)
        apply_icon(self)
        body = tk.Frame(self, bg="#FFFFFF", padx=36, pady=28)
        body.pack(fill="both", expand=True)

        logo = None
        png = resource_path("bwoom.png")
        if os.path.exists(png):
            try:
                logo = tk.PhotoImage(file=png).subsample(3, 3)   # 256px → 약 85px
            except tk.TclError:
                logo = None
        if logo:
            lb = tk.Label(body, image=logo, bg="#FFFFFF")
            lb.image = logo
            lb.pack(pady=(0, 12))

        tk.Label(body, text=APP_TITLE, bg="#FFFFFF", fg="#0F172A",
                 font=(UI_FONT, 16, "bold")).pack()
        tk.Label(body, text=f"버전 {APP_VERSION}", bg="#FFFFFF", fg="#475569",
                 font=(UI_FONT, 10)).pack(pady=(2, 14))
        tk.Label(body, text="뭘 지워도 되는지 알려주는 C드라이브 용량 분석기",
                 bg="#FFFFFF", fg="#334155", font=(UI_FONT, 10)).pack()
        tk.Label(body, text=f"만든 사람: {APP_AUTHOR}", bg="#FFFFFF", fg="#0F172A",
                 font=(UI_FONT, 10, "bold")).pack(pady=(14, 2))
        link = tk.Label(body, text=APP_URL, bg="#FFFFFF", fg="#1F6FEB",
                        font=(UI_FONT, 10, "underline"), cursor="hand2")
        link.pack()
        link.bind("<Button-1>", lambda e: webbrowser.open(APP_URL))
        tk.Label(body, text=f"Copyright (c) 2026 {APP_AUTHOR} · MIT License",
                 bg="#FFFFFF", fg="#64748B", font=(UI_FONT, 9)).pack(pady=(14, 16))
        ttk.Button(body, text="닫기", command=self.destroy).pack()
        self.bind("<Escape>", lambda e: self.destroy())


# ───────────────────────── PM2 관리 창 ─────────────────────────
def find_pm2():
    return shutil.which("pm2") or shutil.which("pm2.cmd")


def run_cmd(args, timeout=60):
    """명령을 콘솔 창 없이 실행하고 (성공 여부, 출력) 반환"""
    if args and args[0].lower().endswith((".cmd", ".bat")):
        args = ["cmd", "/c"] + args
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **kw)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return False, f"시간 초과 ({timeout}초)"
    except OSError as ex:
        return False, str(ex)


def tail_file(path, lines=80, max_bytes=256 * 1024):
    """수 GB짜리 로그도 끝부분만 빠르게 읽기"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    text = data.decode("utf-8", "replace")
    return "\n".join(text.splitlines()[-lines:])


class Pm2Window(tk.Toplevel):
    """PM2 앱 상태 확인, 에러 로그 보기, 중지·삭제, 로그 비우기"""

    def __init__(self, app, logs_node):
        super().__init__(app)
        self.app = app
        self.logs_node = logs_node
        self.pm2 = find_pm2()
        self.apps = {}
        self.busy = False
        self.title("PM2 관리 - " + APP_TITLE)
        self.geometry("1000x620")

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.info = ttk.Label(top, text="")
        self.info.pack(side="left")

        bar = ttk.Frame(self, padding=(8, 0))
        bar.pack(fill="x")
        self.buttons = []

        def btn(text, cmd):
            b = ttk.Button(bar, text=text, command=cmd)
            b.pack(side="left", padx=2)
            self.buttons.append(b)

        btn("새로고침", self.refresh)
        btn("에러 로그 보기", lambda: self.show_log("err"))
        btn("출력 로그 보기", lambda: self.show_log("out"))
        btn("이 앱 중지", lambda: self.app_cmd("stop"))
        btn("이 앱 삭제", lambda: self.app_cmd("delete"))
        btn("이 앱 로그 비우기", lambda: self.app_cmd("flush"))
        btn("전체 로그 비우기", self.flush_all)
        btn("로그 자동 정리 켜기", self.install_logrotate)
        btn("남은 로그 파일 삭제", self.delete_orphans)
        self.orphans = []

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=8)
        lf = ttk.Frame(paned)
        paned.add(lf, weight=1)
        self.list = ttk.Treeview(lf, columns=("id", "status", "restarts", "mem", "err", "out"), show="tree headings")
        self.list.heading("#0", text="앱 이름")
        for col, txt, w in (("id", "ID", 50), ("status", "상태", 90), ("restarts", "재시작 횟수", 100),
                            ("mem", "메모리", 90), ("err", "에러 로그", 110), ("out", "출력 로그", 110)):
            self.list.heading(col, text=txt)
            self.list.column(col, width=w, anchor="e" if col != "status" else "center")
        self.list.column("#0", width=260)
        self.list.tag_configure("bad", foreground="#c0392b")
        App._with_scroll(lf, self.list)
        self.list.bind("<<TreeviewSelect>>", self._on_select)
        self._suppress_log = False
        self._loading = False
        self._last_msg = ""

        tf = ttk.Frame(paned)
        paned.add(tf, weight=2)
        self.text = tk.Text(tf, wrap="word", font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                            insertbackground="#d4d4d4")
        App._with_scroll(tf, self.text)
        self.refresh()

    # --- 공통 ---
    def _set_busy(self, busy, msg=""):
        self.busy = busy
        for b in self.buttons:
            b.config(state="disabled" if busy else "normal")
        if msg:
            self.info.config(text=msg)

    def _run_async(self, args, msg, done, timeout=60):
        """PM2 명령을 백그라운드에서 실행하고, 끝나면 done(ok, out)을 화면 스레드에서 호출"""
        if self.busy:
            return
        self._set_busy(True, msg)

        def work():
            ok, out = run_cmd([self.pm2] + args, timeout)
            self.app.q.put(("call", lambda: self._finish(done, ok, out)))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, done, ok, out):
        if not self.winfo_exists():
            return
        self._set_busy(False)
        done(ok, out)

    def _write(self, text):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.see("end")

    def _on_select(self, _):
        if not self._loading:  # 목록을 다시 그리는 중 생기는 선택 이벤트는 무시
            self.show_log("err")

    def _selected(self):
        sel = self.list.selection()
        return self.apps.get(sel[0]) if sel else None

    # --- 목록 ---
    def refresh(self):
        if not self.pm2:
            self._show_files_only()
            return
        self._run_async(["jlist"], "PM2 앱 목록을 불러오는 중…", self._on_list, timeout=30)

    def _on_list(self, ok, out):
        import json
        prev = self._selected()
        self._prev_id = prev["id"] if prev else None
        self._loading = True
        self.after(150, lambda: setattr(self, "_loading", False))
        self.list.delete(*self.list.get_children())
        self.apps.clear()
        procs = None
        dec = json.JSONDecoder()
        for i, ch in enumerate(out):  # "[PM2] ..." 같은 안내 문구는 건너뛰고 실제 JSON 목록을 찾음
            if ch == "[":
                try:
                    v, _ = dec.raw_decode(out, i)
                except ValueError:
                    continue
                if isinstance(v, list):
                    procs = v
                    break
        if procs is None:
            self.info.config(text="PM2 목록을 읽지 못했습니다.")
            self._write(out)
            return
        for p in procs:
            env = p.get("pm2_env", {})
            a = {"id": p.get("pm_id"), "name": p.get("name", "?"), "status": env.get("status", "?"),
                 "restarts": env.get("restart_time", 0), "mem": p.get("monit", {}).get("memory", 0),
                 "err": env.get("pm_err_log_path"), "out": env.get("pm_out_log_path")}
            sz = lambda f: fmt_size(os.path.getsize(f)) if f and os.path.exists(f) else "-"
            bad = a["status"] in ("errored", "stopped") or a["restarts"] >= 10
            iid = self.list.insert("", "end", text=a["name"], tags=("bad",) if bad else (),
                                   values=(a["id"], a["status"], f"{a['restarts']:,}", fmt_size(a["mem"]),
                                           sz(a["err"]), sz(a["out"])))
            self.apps[iid] = a
        used = {_norm(a[k]) for a in self.apps.values() for k in ("err", "out") if a[k]}
        self.orphans = []
        try:
            names = sorted(os.listdir(self.logs_node.path))
        except OSError:
            names = []
        for fn in names:
            fp = os.path.join(self.logs_node.path, fn)
            if fn == "pm2.log" or not os.path.isfile(fp) or _norm(fp) in used:
                continue
            size = os.path.getsize(fp)
            self.orphans.append((fp, size))
            iid = self.list.insert("", "end", text=f"(삭제된 앱의 로그) {fn}", tags=("bad",),
                                   values=("", "정리 대상", "", "", fmt_size(size), ""))
            self.apps[iid] = {"id": None, "name": fn, "err": fp, "out": fp}
        orphan_total = sum(sz for _, sz in self.orphans)
        total = sum(c.size for c in self.logs_node.children) if self.logs_node.children else 0
        prefix = self._last_msg + "    ▶    " if self._last_msg else ""
        self._last_msg = ""
        orphan_txt = (f"  |  삭제된 앱의 로그 {fmt_size(orphan_total)} → [남은 로그 파일 삭제]"
                      if self.orphans else "")
        self.info.config(text=f"{prefix}PM2 앱 {len(procs)}개  |  로그 폴더 {fmt_size(total)}{orphan_txt}  |  "
                              f"빨간색 = 문제 있는 앱/정리 대상")
        kids = self.list.get_children()
        if kids:
            keep = next((i for i in kids if self.apps[i]["id"] == self._prev_id), kids[0])
            self.list.selection_set(keep)
            if self._suppress_log:  # 방금 작업 결과를 보여주는 중이면 로그로 덮지 않음
                self._suppress_log = False
            else:
                self.show_log("err")
        else:
            self._suppress_log = False
            self._write("PM2에 등록된 앱이 없습니다.")

    def _show_files_only(self):
        """PM2가 설치돼 있지 않거나 PATH에 없을 때: 로그 파일만 보여줌"""
        self.info.config(text="PM2 명령을 찾지 못했습니다 (Node.js/PM2 미설치 또는 PATH 문제). 로그 내용 보기만 가능합니다.")
        for b in self.buttons[3:]:
            b.config(state="disabled")
        self.list.delete(*self.list.get_children())
        self.apps.clear()
        for c in self.logs_node.children:
            if c.is_dir:
                continue
            iid = self.list.insert("", "end", text=c.name, values=("", "", "", "", fmt_size(c.size), ""))
            self.apps[iid] = {"id": None, "name": c.name, "err": c.path, "out": c.path}

    # --- 동작 ---
    def show_log(self, which):
        a = self._selected()
        if not a:
            return
        path = a.get(which)
        if not path or not os.path.exists(path):
            self._write("로그 파일이 없습니다.")
            return
        try:
            body = tail_file(path)
        except OSError as ex:
            body = f"읽지 못했습니다: {ex}"
        head = f"── {path}  ({fmt_size(os.path.getsize(path))}) — 마지막 80줄 ──\n\n"
        self._write(head + (body or "(비어 있음)"))

    def app_cmd(self, action):
        a = self._selected()
        if not a or a["id"] is None:
            return
        names = {"stop": "중지", "delete": "삭제", "flush": "로그 비우기"}
        extra = {"stop": "\n\n앱이 멈춥니다. 다시 켜려면 'pm2 start'가 필요합니다.",
                 "delete": "\n\n앱이 PM2 목록에서 빠집니다. 앱 파일(소스 코드)은 지워지지 않습니다.",
                 "flush": "\n\n이 앱의 로그 내용을 비웁니다."}[action]
        if not messagebox.askyesno("PM2", f"'{a['name']}' 앱을 {names[action]}할까요?{extra}", parent=self):
            return
        self._run_async([action, str(a["id"])], f"{a['name']} {names[action]} 중…",
                        lambda ok, out: self._after_change(ok, out, f"{a['name']} {names[action]}"))

    def flush_all(self):
        if not messagebox.askyesno("PM2", "모든 PM2 앱의 로그 내용을 비울까요?", parent=self):
            return
        self._run_async(["flush"], "전체 로그 비우는 중…",
                        lambda ok, out: self._after_change(ok, out, "전체 로그 비우기"))

    def delete_orphans(self):
        """PM2에서 이미 삭제된 앱의 로그 파일은 pm2 flush로 안 지워지므로 직접 삭제"""
        if not self.orphans:
            messagebox.showinfo("PM2", "삭제된 앱이 남긴 로그 파일이 없습니다.", parent=self)
            return
        total = sum(sz for _, sz in self.orphans)
        names = "\n".join(os.path.basename(p) for p, _ in self.orphans[:10])
        if not messagebox.askyesno(
                "남은 로그 파일 삭제",
                f"PM2에서 이미 삭제된 앱의 로그 파일 {len(self.orphans)}개 ({fmt_size(total)})를 삭제할까요?\n\n"
                f"{names}\n\n로그는 휴지통에 넣어도 공간이 늘지 않아서 영구 삭제합니다.", parent=self):
            return
        failed = []
        for fp, _ in self.orphans:
            try:
                os.remove(fp)
            except OSError:
                failed.append(os.path.basename(fp))
        msg = "남은 로그 파일 삭제: 완료"
        if failed:
            msg += f" (사용 중이라 못 지운 파일 {len(failed)}개: {', '.join(failed[:3])} — 'pm2 kill' 후 다시 시도)"
        self._after_change(not failed, "", msg.split(":")[0])
        if failed:
            self._write(msg)

    def install_logrotate(self):
        if not messagebox.askyesno(
                "PM2", "pm2-logrotate를 설치할까요?\n\n로그 파일이 10MB를 넘으면 자동으로 잘라 보관하고, "
                       "오래된 로그는 지워서 다시 쌓이지 않게 합니다. (인터넷 연결 필요, 1~2분 소요)", parent=self):
            return
        self._run_async(["install", "pm2-logrotate"], "pm2-logrotate 설치 중… (1~2분)",
                        lambda ok, out: self._after_change(ok, out, "로그 자동 정리 설치"), timeout=300)

    def _after_change(self, ok, out, what):
        before = self.logs_node.size
        self.app.resync_files(self.logs_node)
        freed = before - self.logs_node.size
        result = "완료" if ok else "실패"
        msg = f"{what}: {result}"
        if freed > 0:
            msg += f"  —  {fmt_size(freed)} 확보"
            self.app.status.config(text=f"PM2 로그 정리로 {fmt_size(freed)} 확보")
        self._write(msg + "\n\n" + out[-4000:])
        self._last_msg = msg
        self._suppress_log = True
        self.refresh()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Bwoom.DiskCleaner")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 고해상도 화면에서 흐림 방지
        except Exception:
            pass
    sys.setrecursionlimit(10000)
    App().mainloop()


if __name__ == "__main__":
    main()
