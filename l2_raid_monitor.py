"""
L2 Raid Monitor - Monitor da janela "World Info > Raid" do Lineage 2.

Funcionalidades:
  * Clica automaticamente no botão "Raid" em intervalos configuráveis
    (por coordenada calibrada ou localizando a imagem do botão com OpenCV).
  * Captura a região da lista de bosses, divide em linhas e compara cada linha
    com a referência (diferença de cor/pixels). OCR opcional (Tesseract) para
    ler o nome e o status ("Standby" etc).
  * Alerta sonoro nativo do Windows (winsound) + janela de destaque sempre
    no topo + notificação toast (plyer, opcional).
  * Interface Tkinter: Iniciar / Parar, intervalo, calibração de regiões.

Requisitos: pip install -r requirements.txt
Execute como Administrador se o jogo estiver rodando como Administrador
(senão o Windows bloqueia os cliques simulados).
"""

import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

import numpy as np
import cv2
import pyautogui
import requests
import keyboard  # Adiciona esta importação no topo do arquivo
from PIL import Image, ImageGrab, ImageTk

try:
    import winsound
except ImportError:  # fora do Windows
    winsound = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from plyer import notification as plyer_notification
except ImportError:
    plyer_notification = None

# Habilita coordenadas reais em monitores com escala (125%, 150%...)
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

pyautogui.FAILSAFE = True  # mover o mouse para o canto superior esquerdo aborta
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(APP_DIR, "l2_raid_config.json")
TEMPLATE_FILE = os.path.join(APP_DIR, "raid_button_template.png")

DEFAULT_CONFIG = {
    "list_region": None,          # [x, y, w, h]
    "raid_button_point": None,    # [x, y]
    "use_template": False,
    "click_interval": 30.0,       # segundos entre cliques no Raid
    "check_delay": 1.5,           # espera após o clique antes de capturar
    "rows": 20,                   # nº de linhas visíveis na lista
    "threshold": 30.0,            # diferença mínima de COR DO TEXTO (0-255) para alertar
    "click_count": 2,             # quantos cliques por vez no Raid
    "click_hold": 0.08,           # tempo segurando o botão do mouse (s)
    "use_ocr": False,
    "tesseract_path": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    "boss_names": "",             # opcional: um nome por linha, na ordem da lista
    "restore_mouse": True,
    "beep_repeat": 3,
}


# ----------------------------------------------------------------- utilidades
def grab(region):
    x, y, w, h = region
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def split_rows(img, rows):
    h = img.shape[0]
    step = h / rows
    return [img[int(i * step):int((i + 1) * step), :] for i in range(rows)]


def text_mask(row):
    """Pixels do TEXTO: bem mais claros que o fundo da linha (ignora caixa/fundo)."""
    bright = row.max(axis=2).astype(np.int16)  # brilho pelo canal mais forte (vale p/ texto vermelho, verde...)
    bg = int(np.median(bright))
    return bright > bg + 45


def row_signature(row):
    """Assinatura = cor média só dos pixels de texto + quantidade de pixels de texto."""
    mask = text_mask(row)
    n = int(mask.sum())
    if n < 12:
        return None  # linha vazia (lista sem conteúdo)
    color = np.array([row[:, :, c][mask].mean() for c in range(3)], dtype=np.float32)
    return {"color": color, "n": n}


def row_diff(a, b):
    """Diferença de cor do texto. None = não comparável (linha vazia)."""
    if a is None or b is None:
        return None
    # se o texto mudou demais de tamanho, provavelmente a lista não carregou direito
    if a["n"] < b["n"] * 0.4:
        return None
    return float(np.max(np.abs(a["color"] - b["color"])))


def dominant_text_color(row):
    """Cor média dos pixels mais claros (o texto) - ajuda a descrever a mudança."""
    gray = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
    thr = np.percentile(gray, 90)
    mask = gray >= thr
    if not mask.any():
        return (0, 0, 0)
    b, g, r = [int(row[:, :, c][mask].mean()) for c in range(3)]
    return (r, g, b)


def ocr_row(row):
    if pytesseract is None:
        return ""
    big = cv2.resize(row, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(th) < 127:  # texto claro em fundo escuro -> inverter
        th = 255 - th
    try:
        txt = pytesseract.image_to_string(th, config="--psm 7")
    except Exception:
        return ""
    return " ".join(txt.split())


# ------------------------------------------------- seletor de região na tela
class RegionSelector:
    """Overlay em tela cheia; arraste para selecionar retângulo ou clique para ponto."""

    def __init__(self, root, mode="rect", hint=""):
        self.result = None
        self.mode = mode
        shot = ImageGrab.grab(all_screens=True)
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.geometry(f"{shot.width}x{shot.height}+0+0")
        self.top.attributes("-topmost", True)
        self.photo = ImageTk.PhotoImage(shot)
        self.canvas = tk.Canvas(self.top, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(0, 0, shot.width, 40, fill="black", stipple="gray50")
        self.canvas.create_text(20, 20, anchor="w", fill="yellow", font=("Segoe UI", 14, "bold"),
                                text=hint + "   (ESC para cancelar)")
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", lambda e: self.top.destroy())
        self.top.focus_force()
        self.top.grab_set()
        root.wait_window(self.top)

    def on_press(self, e):
        self.start = (e.x, e.y)
        if self.mode == "point":
            self.result = (e.x, e.y)
            self.top.destroy()

    def on_drag(self, e):
        if self.mode != "rect" or not self.start:
            return
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(*self.start, e.x, e.y, outline="red", width=2)

    def on_release(self, e):
        if self.mode != "rect" or not self.start:
            return
        x1, y1 = self.start
        x, y, w, h = min(x1, e.x), min(y1, e.y), abs(e.x - x1), abs(e.y - y1)
        if w > 5 and h > 5:
            self.result = (x, y, w, h)
        self.top.destroy()


# ------------------------------------------------------------------- alertas


class Alerts:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        # Webhook do Discord configurado
        self.webhook_url = "https://discord.com/api/webhooks/1421480218304057434/Q3U1ltjbDDTEfK7Bi6FvDzNpO2NhM8t3I6euSwYsYijfeafrf6T00Nyup9_i86Wn_tiy"

    def sound(self):
        # Opcional: Se quiseres manter o som de alerta no PC, deixa esta linha. 
        # Se não quiseres nenhum som no PC, podes apagar o conteúdo desta função.
        def _play():
            if winsound:
                for _ in range(int(self.cfg.get("beep_repeat", 3))):
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    winsound.Beep(1200, 250)
                    winsound.Beep(900, 250)
            else:
                print("\a")
        threading.Thread(target=_play, daemon=True).start()

    def toast(self, title, msg):
        # Envia exclusivamente para o Discord via Webhook POST
        try:
            content = f"**{title}**\n```{msg}```"
            payload = {"content": content}
            requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception as e:
            print(f"Erro ao enviar para o Discord: {e}")

    def popup(self, title, lines):
        # Vazio de propósito para NÃO abrir nenhuma janela de aviso na ecrã do PC
        pass

    def fire(self, changes):
        title = f"Boss mudou de status! ({datetime.now():%H:%M:%S})"
        self.sound() # Mantém o bip sonoro (opcional)
        self.toast(title, "\n".join(changes)) # Envia para o Discord
        # O popup foi desativado aqui para não saltar nenhuma janela no computador


# ------------------------------------------------------------------ monitor
class Monitor(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.stop_evt = threading.Event()
        self.baseline = None
        self.baseline_text = None

    def log(self, msg):
        self.app.log(msg)

    def click_raid(self):
        cfg = self.app.cfg
        point = None
        if cfg["use_template"] and os.path.exists(TEMPLATE_FILE):
            try:
                loc = pyautogui.locateCenterOnScreen(TEMPLATE_FILE, confidence=0.8)
                if loc:
                    point = (int(loc.x), int(loc.y))
            except Exception as e:
                self.log(f"Template não encontrado: {e}")
        if point is None and cfg["raid_button_point"]:
            point = tuple(cfg["raid_button_point"])
        if point is None:
            self.log("Botão Raid não calibrado - pulando clique.")
            return
        prev = pyautogui.position()
        # Jogos como o L2 ignoram cliques "instantâneos": move, espera e segura o botão.
        pyautogui.moveTo(*point, duration=0.15)
        time.sleep(0.1)
        for _ in range(max(1, int(cfg.get("click_count", 2)))):
            pyautogui.mouseDown()
            time.sleep(float(cfg.get("click_hold", 0.08)))
            pyautogui.mouseUp()
            time.sleep(0.15)
        if cfg["restore_mouse"]:
            pyautogui.moveTo(*prev)
        self.log(f"Clique no Raid em {point}")

    def label_for(self, i, row):
        names = [n.strip() for n in self.app.cfg["boss_names"].splitlines() if n.strip()]
        if i < len(names):
            return names[i]
        if self.baseline_text and self.baseline_text[i]:
            return self.baseline_text[i]
        return f"Linha {i + 1}"

    def capture(self):
        cfg = self.app.cfg
        img = grab(cfg["list_region"])
        rows = split_rows(img, int(cfg["rows"]))
        return img, rows

    def run(self):
        cfg = self.app.cfg
        if cfg["use_ocr"] and pytesseract and os.path.exists(cfg["tesseract_path"]):
            pytesseract.pytesseract.tesseract_cmd = cfg["tesseract_path"]
        self.log("Monitoramento iniciado.")
        next_click = 0.0
        while not self.stop_evt.is_set():
            try:
                now = time.time()
                if now >= next_click:
                    self.click_raid()
                    next_click = now + float(cfg["click_interval"])
                    if self.stop_evt.wait(float(cfg["check_delay"])):
                        break
                img, rows = self.capture()
                self.app.show_preview(img)
                sigs = [row_signature(r) for r in rows]
                if sum(s is not None for s in sigs) < max(1, len(sigs) // 3):
                    self.log("Lista vazia/carregando - ignorando esta leitura.")
                    if self.stop_evt.wait(1.0):
                        break
                    continue
                use_ocr = cfg["use_ocr"] and pytesseract is not None
                if self.baseline is None:
                    self.baseline = sigs
                    self.baseline_text = [ocr_row(r) for r in rows] if use_ocr else None
                    self.log(f"Referência capturada ({len(rows)} linhas).")
                else:
                    changes = []
                    for i, (row, s) in enumerate(zip(rows, sigs)):
                        if self.baseline[i] is None and s is not None:
                            self.baseline[i] = s  # linha que estava vazia ganhou conteúdo
                            continue
                        d = row_diff(s, self.baseline[i])
                        if d is None:
                            continue  # linha vazia agora: não alerta
                        txt_changed = False
                        new_txt = ""
                        if use_ocr:
                            new_txt = ocr_row(row)
                            old = self.baseline_text[i]
                            # só considera mudança de status (ex.: Standby sumiu)
                            if old and new_txt and ("standby" in old.lower()) != ("standby" in new_txt.lower()):
                                txt_changed = True
                        if d > float(cfg["threshold"]) or txt_changed:
                            r, g, b = dominant_text_color(row)
                            name = self.label_for(i, row)
                            detail = f" → '{new_txt}'" if new_txt else ""
                            changes.append(f"{name}: mudou (dif {d:.1f}, cor texto #{r:02x}{g:02x}{b:02x}){detail}")
                            self.baseline[i] = s
                            if use_ocr:
                                self.baseline_text[i] = new_txt
                    if changes:
                        for c in changes:
                            self.log("ALERTA: " + c)
                        self.app.root.after(0, self.app.alerts.fire, changes)
            except pyautogui.FailSafeException:
                self.log("Fail-safe acionado (mouse no canto). Parando.")
                break
            except Exception as e:
                self.log(f"Erro: {e}")
            if self.stop_evt.wait(1.0):
                break
        self.log("Monitoramento parado.")
        self.app.root.after(0, self.app.on_stopped)


# ----------------------------------------------------------------------- GUI
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = self.load_cfg()
        self.alerts = Alerts(root, self.cfg)
        self.monitor = None
        root.title("L2 Raid Monitor")
        root.geometry("560x720")
        root.attributes("-topmost", False)
        self.build()
        self.refresh_labels()
    # Adiciona esta chamada no final do método __init__ da classe App:
        self.setup_hotkeys()

    def setup_hotkeys(self):
        # Hotkey global para iniciar (ex: F5)
        keyboard.add_hotkey('F9', lambda: self.root.after(0, self.safe_start))
        # Hotkey global para parar (ex: F6)
        keyboard.add_hotkey('F10', lambda: self.root.after(0, self.stop))

    def safe_start(self):
        # Só dispara o start se o botão estiver habilitado (evita duplo clique)
        if str(self.b_start['state']) != 'disabled':
            self.start()

    # ---- config
    def load_cfg(self):
        cfg = dict(DEFAULT_CONFIG)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg.update(json.load(f))
            except Exception:
                pass
        return cfg

    def save_cfg(self):
        self.pull_fields()
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2, ensure_ascii=False)
        self.log("Configuração salva.")

    def pull_fields(self):
        try:
            self.cfg["click_interval"] = max(1.0, float(self.v_interval.get()) * (60 if self.v_unit.get() == "minutos" else 1))
            self.cfg["rows"] = max(1, int(self.v_rows.get()))
            self.cfg["threshold"] = float(self.v_thr.get())
            self.cfg["check_delay"] = float(self.v_delay.get())
            self.cfg["click_count"] = max(1, int(self.v_clicks.get()))
        except ValueError:
            messagebox.showerror("Erro", "Valores numéricos inválidos.")
            raise
        self.cfg["use_ocr"] = self.v_ocr.get()
        self.cfg["use_template"] = self.v_tpl.get()
        self.cfg["restore_mouse"] = self.v_restore.get()
        self.cfg["boss_names"] = self.t_names.get("1.0", "end").strip()
        if self.cfg["list_region"] is None:
            for k, v in zip(("x", "y", "w", "h"), (self.v_x, self.v_y, self.v_w, self.v_h)):
                pass
        try:
            vals = [int(v.get()) for v in (self.v_x, self.v_y, self.v_w, self.v_h)]
            if vals[2] > 0 and vals[3] > 0:
                self.cfg["list_region"] = vals
        except ValueError:
            pass
        try:
            self.cfg["raid_button_point"] = [int(self.v_bx.get()), int(self.v_by.get())]
        except ValueError:
            pass

    # ---- layout
    def build(self):
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        self.b_start = ttk.Button(top, text="▶ Iniciar Monitoramento", command=self.start)
        self.b_start.pack(side="left", padx=4)
        self.b_stop = ttk.Button(top, text="■ Parar", command=self.stop, state="disabled")
        self.b_stop.pack(side="left", padx=4)
        ttk.Button(top, text="Testar alerta", command=lambda: self.alerts.fire(["[Lv.90] Antharas: teste"])).pack(side="left", padx=4)
        ttk.Button(top, text="Salvar", command=self.save_cfg).pack(side="right", padx=4)
        self.status = ttk.Label(self.root, text="Parado", foreground="gray")
        self.status.pack(anchor="w", padx=12)

        f1 = ttk.LabelFrame(self.root, text="Botão Raid")
        f1.pack(fill="x", **pad)
        self.v_bx, self.v_by = tk.StringVar(), tk.StringVar()
        ttk.Label(f1, text="X").grid(row=0, column=0)
        ttk.Entry(f1, textvariable=self.v_bx, width=6).grid(row=0, column=1)
        ttk.Label(f1, text="Y").grid(row=0, column=2)
        ttk.Entry(f1, textvariable=self.v_by, width=6).grid(row=0, column=3)
        ttk.Button(f1, text="Clicar na tela p/ calibrar", command=self.pick_button).grid(row=0, column=4, padx=6)
        ttk.Button(f1, text="Capturar imagem do botão", command=self.pick_template).grid(row=0, column=5)
        self.v_tpl = tk.BooleanVar(value=self.cfg["use_template"])
        ttk.Checkbutton(f1, text="Localizar botão por imagem (OpenCV)", variable=self.v_tpl).grid(row=1, column=0, columnspan=4, sticky="w")
        self.v_restore = tk.BooleanVar(value=self.cfg["restore_mouse"])
        ttk.Checkbutton(f1, text="Devolver mouse à posição", variable=self.v_restore).grid(row=1, column=4, columnspan=2, sticky="w")

        ttk.Label(f1, text="Intervalo:").grid(row=2, column=0, columnspan=2, sticky="e")
        iv = self.cfg["click_interval"]
        unit = "minutos" if iv >= 60 and iv % 60 == 0 else "segundos"
        self.v_interval = tk.StringVar(value=str(int(iv / 60) if unit == "minutos" else iv))
        ttk.Entry(f1, textvariable=self.v_interval, width=6).grid(row=2, column=2, columnspan=2)
        self.v_unit = tk.StringVar(value=unit)
        ttk.Combobox(f1, textvariable=self.v_unit, values=["segundos", "minutos"], width=9, state="readonly").grid(row=2, column=4, sticky="w")
        ttk.Label(f1, text="Cliques por vez:").grid(row=3, column=0, columnspan=2, sticky="e")
        self.v_clicks = tk.StringVar(value=str(self.cfg.get("click_count", 2)))
        ttk.Entry(f1, textvariable=self.v_clicks, width=6).grid(row=3, column=2, columnspan=2)
        ttk.Button(f1, text="Testar clique", command=lambda: (self.pull_fields(), threading.Thread(target=Monitor(self).click_raid, daemon=True).start())).grid(row=3, column=4, sticky="w")

        f2 = ttk.LabelFrame(self.root, text="Região da lista de bosses")
        f2.pack(fill="x", **pad)
        self.v_x, self.v_y, self.v_w, self.v_h = (tk.StringVar() for _ in range(4))
        for i, (lbl, var) in enumerate(zip("XYWH", (self.v_x, self.v_y, self.v_w, self.v_h))):
            ttk.Label(f2, text=lbl).grid(row=0, column=i * 2)
            ttk.Entry(f2, textvariable=var, width=6).grid(row=0, column=i * 2 + 1)
        ttk.Button(f2, text="Selecionar arrastando", command=self.pick_region).grid(row=0, column=8, padx=6)
        self.v_rows = tk.StringVar(value=str(self.cfg["rows"]))
        self.v_thr = tk.StringVar(value=str(self.cfg["threshold"]))
        self.v_delay = tk.StringVar(value=str(self.cfg["check_delay"]))
        ttk.Label(f2, text="Linhas visíveis").grid(row=1, column=0, columnspan=2)
        ttk.Entry(f2, textvariable=self.v_rows, width=6).grid(row=1, column=2, columnspan=2)
        ttk.Label(f2, text="Dif. cor do texto").grid(row=1, column=4, columnspan=2)
        ttk.Entry(f2, textvariable=self.v_thr, width=6).grid(row=1, column=6, columnspan=2)
        ttk.Label(f2, text="Espera pós-clique (s)").grid(row=2, column=0, columnspan=3)
        ttk.Entry(f2, textvariable=self.v_delay, width=6).grid(row=2, column=3, columnspan=2)
        self.v_ocr = tk.BooleanVar(value=self.cfg["use_ocr"])
        ttk.Checkbutton(f2, text="OCR (Tesseract)", variable=self.v_ocr).grid(row=2, column=5, columnspan=3)
        ttk.Button(f2, text="Tesseract...", command=self.pick_tesseract).grid(row=2, column=8)

        f3 = ttk.LabelFrame(self.root, text="Nomes dos bosses (opcional, um por linha, na ordem da lista)")
        f3.pack(fill="x", **pad)
        self.t_names = tk.Text(f3, height=4)
        self.t_names.pack(fill="x")
        self.t_names.insert("1.0", self.cfg["boss_names"])

        f4 = ttk.LabelFrame(self.root, text="Pré-visualização")
        f4.pack(fill="x", **pad)
        self.preview = ttk.Label(f4)
        self.preview.pack()

        f5 = ttk.LabelFrame(self.root, text="Log")
        f5.pack(fill="both", expand=True, **pad)
        self.t_log = tk.Text(f5, height=8, state="disabled", bg="#111", fg="#ddd")
        self.t_log.pack(fill="both", expand=True)

    def refresh_labels(self):
        r = self.cfg["list_region"]
        if r:
            for var, val in zip((self.v_x, self.v_y, self.v_w, self.v_h), r):
                var.set(str(val))
        p = self.cfg["raid_button_point"]
        if p:
            self.v_bx.set(str(p[0])); self.v_by.set(str(p[1]))

    # ---- calibração
    def _hide_and(self, fn):
        self.root.withdraw()
        self.root.after(250, lambda: (fn(), self.root.deiconify()))

    def pick_button(self):
        def go():
            s = RegionSelector(self.root, "point", "Clique no centro do botão 'Raid'")
            if s.result:
                self.cfg["raid_button_point"] = list(s.result)
                self.refresh_labels()
                self.log(f"Botão Raid calibrado em {s.result}")
        self._hide_and(go)

    def pick_template(self):
        def go():
            s = RegionSelector(self.root, "rect", "Arraste ao redor do botão 'Raid'")
            if s.result:
                x, y, w, h = s.result
                ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(TEMPLATE_FILE)
                self.cfg["raid_button_point"] = [x + w // 2, y + h // 2]
                self.v_tpl.set(True)
                self.refresh_labels()
                self.log("Imagem do botão Raid salva.")
        self._hide_and(go)

    def pick_region(self):
        def go():
            s = RegionSelector(self.root, "rect", "Arraste ao redor da LISTA de bosses (sem a barra de rolagem)")
            if s.result:
                self.cfg["list_region"] = list(s.result)
                self.refresh_labels()
                self.log(f"Região da lista: {s.result}")
                self.show_preview(grab(s.result))
        self._hide_and(go)

    def pick_tesseract(self):
        p = filedialog.askopenfilename(title="tesseract.exe", filetypes=[("Executável", "*.exe"), ("Todos", "*.*")])
        if p:
            self.cfg["tesseract_path"] = p

    # ---- controle
    def start(self):
        try:
            self.pull_fields()
        except ValueError:
            return
        if not self.cfg["list_region"]:
            messagebox.showwarning("Calibração", "Selecione a região da lista de bosses primeiro.")
            return
        if self.cfg["use_ocr"] and pytesseract is None:
            messagebox.showwarning("OCR", "pytesseract não instalado - OCR desativado.")
            self.cfg["use_ocr"] = False
        self.save_cfg()
        self.monitor = Monitor(self)
        self.monitor.start()
        self.b_start.config(state="disabled")
        self.b_stop.config(state="normal")
        self.status.config(text="Monitorando...", foreground="green")

    def stop(self):
        if self.monitor:
            self.monitor.stop_evt.set()

    def on_stopped(self):
        self.b_start.config(state="normal")
        self.b_stop.config(state="disabled")
        self.status.config(text="Parado", foreground="gray")

    # ---- thread-safe UI helpers
    def log(self, msg):
        line = f"[{datetime.now():%H:%M:%S}] {msg}\n"
        def _w():
            self.t_log.config(state="normal")
            self.t_log.insert("end", line)
            self.t_log.see("end")
            self.t_log.config(state="disabled")
        self.root.after(0, _w)

    def show_preview(self, bgr):
        def _s():
            img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            img.thumbnail((520, 160))
            self._pv = ImageTk.PhotoImage(img)
            self.preview.config(image=self._pv)
        self.root.after(0, _s)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
